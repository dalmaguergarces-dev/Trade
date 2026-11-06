"""Real swaps on Solana through Jupiter, signed by the bot's own wallet.

The private key comes only from the SOLANA_BOT_KEY environment variable (a
GitHub Actions secret). It is never printed, logged or written to disk.
"""

import base64
import json
import os
import time
import urllib.error
import urllib.request

from solders.keypair import Keypair
from solders.transaction import VersionedTransaction

JUP = "https://lite-api.jup.ag/swap/v1"
DEFAULT_RPC = "https://api.mainnet-beta.solana.com"
USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
SOL = "So11111111111111111111111111111111111111112"


class SwapError(Exception):
    pass


def _http(url, body=None, timeout=30):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers={
        "Content-Type": "application/json", "Accept": "application/json",
        "User-Agent": "paper-trading-bot"})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            msg = e.read().decode(errors="replace")[:300]
            if e.code in (429, 500, 502, 503) and attempt < 3:
                time.sleep(2 * (attempt + 1))
                continue
            raise SwapError(f"HTTP {e.code} from {url.split('?')[0]}: {msg}")
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt == 3:
                raise SwapError(f"network error: {e}")
            time.sleep(2 * (attempt + 1))


def load_keypair(secret):
    """Accept Phantom's exported base58 private key or a JSON byte array."""
    secret = secret.strip()
    if secret.startswith("["):
        return Keypair.from_bytes(bytes(json.loads(secret)))
    return Keypair.from_base58_string(secret)


class Wallet:
    def __init__(self, secret, rpc_url=None):
        self.kp = load_keypair(secret)
        self.pubkey = str(self.kp.pubkey())
        self.rpc_url = rpc_url or DEFAULT_RPC
        self._decimals = {USDC: 6, SOL: 9}

    def __repr__(self):  # never expose the key
        return f"Wallet({self.pubkey})"

    # ---- RPC -------------------------------------------------------------
    def rpc(self, method, params):
        r = _http(self.rpc_url, {"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        if "error" in r:
            raise SwapError(f"RPC {method}: {r['error']}")
        return r["result"]

    def decimals(self, mint):
        if mint not in self._decimals:
            self._decimals[mint] = self.rpc("getTokenSupply", [mint])["value"]["decimals"]
        return self._decimals[mint]

    def sol_lamports(self):
        return self.rpc("getBalance", [self.pubkey, {"commitment": "confirmed"}])["value"]

    def token_raw(self, mint):
        """Raw token amount held (all token accounts for that mint)."""
        if mint == SOL:
            return self.sol_lamports()
        res = self.rpc("getTokenAccountsByOwner", [self.pubkey, {"mint": mint},
                                                    {"encoding": "jsonParsed", "commitment": "confirmed"}])
        return sum(int(a["account"]["data"]["parsed"]["info"]["tokenAmount"]["amount"]) for a in res["value"])

    # ---- Jupiter -----------------------------------------------------------
    def quote(self, in_mint, out_mint, amount_raw, slippage_bps):
        q = _http(f"{JUP}/quote?inputMint={in_mint}&outputMint={out_mint}&amount={int(amount_raw)}"
                  f"&slippageBps={int(slippage_bps)}&restrictIntermediateTokens=true")
        if "outAmount" not in q:
            raise SwapError(f"no quote: {str(q)[:200]}")
        return q

    def _build(self, quote):
        r = _http(f"{JUP}/swap", {
            "quoteResponse": quote,
            "userPublicKey": self.pubkey,
            "wrapAndUnwrapSol": True,
            "dynamicComputeUnitLimit": True,
            "prioritizationFeeLamports": {"priorityLevelWithMaxLamports": {
                "maxLamports": 100_000, "priorityLevel": "high"}},  # cap ~0.0001 SOL
        })
        if "swapTransaction" not in r:
            raise SwapError(f"swap build failed: {str(r)[:200]}")
        raw = VersionedTransaction.from_bytes(base64.b64decode(r["swapTransaction"]))
        signed = VersionedTransaction(raw.message, [self.kp])
        return base64.b64encode(bytes(signed)).decode()

    def simulate(self, quote):
        """Build and sign the swap, then ask the network to simulate it."""
        tx = self._build(quote)
        res = self.rpc("simulateTransaction", [tx, {"encoding": "base64", "sigVerify": True,
                                                    "commitment": "confirmed"}])["value"]
        return {"ok": res.get("err") is None, "err": res.get("err"),
                "logs": (res.get("logs") or [])[-3:]}

    def execute(self, quote, in_mint, out_mint, timeout=75):
        """Send the swap and wait for confirmation.

        Returns the actual raw amounts spent and received, read from the
        confirmed transaction (not the quote).
        """
        tx = self._build(quote)
        sig = self.rpc("sendTransaction", [tx, {"encoding": "base64", "skipPreflight": False,
                                                "preflightCommitment": "confirmed", "maxRetries": 5}])
        deadline = time.time() + timeout
        while time.time() < deadline:
            time.sleep(3)
            st = self.rpc("getSignatureStatuses", [[sig]])["value"][0]
            if st and st.get("err"):
                raise SwapError(f"transaction {sig} failed: {st['err']}")
            if st and st.get("confirmationStatus") in ("confirmed", "finalized"):
                spent, got = self._deltas(sig, in_mint, out_mint)
                return {"signature": sig, "spent_raw": spent, "received_raw": got}
        raise SwapError(f"transaction {sig} not confirmed within {timeout}s (it may still land)")

    def _deltas(self, sig, in_mint, out_mint):
        tx = None
        for _ in range(10):
            tx = self.rpc("getTransaction", [sig, {"encoding": "jsonParsed", "commitment": "confirmed",
                                                   "maxSupportedTransactionVersion": 0}])
            if tx:
                break
            time.sleep(2)
        if not tx:
            raise SwapError(f"couldn't read transaction {sig}")
        meta = tx["meta"]

        def change(mint):
            if mint == SOL:  # native SOL, excluding the network fee we paid
                return meta["postBalances"][0] - meta["preBalances"][0] + meta["fee"]
            def total(rows):
                return sum(int(b["uiTokenAmount"]["amount"]) for b in rows or []
                           if b.get("owner") == self.pubkey and b["mint"] == mint)
            return total(meta.get("postTokenBalances")) - total(meta.get("preTokenBalances"))

        return -change(in_mint), change(out_mint)
