# -*- coding: utf-8 -*-
"""
Test TUSHARE_TOKEN connectivity and stock data fetching for STOCK_LIST.
Includes YfinanceFetcher tests for US stocks.

Verifies:
1. TUSHARE_TOKEN is configured and valid
2. Tushare API can be initialized successfully
3. Each stock in STOCK_LIST can be fetched (or correctly rejected if unsupported)
4. US stocks in STOCK_LIST can be fetched via YfinanceFetcher

Usage:
    python -m pytest tests/test_tushare_stock_fetch.py -v
    python tests/test_tushare_stock_fetch.py          # standalone
"""

import logging
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import dotenv_values

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_US_PATTERN = re.compile(r"^[A-Z]{1,5}(\.[A-Z])?$")

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"


def _load_env_config():
    """Load TUSHARE_TOKEN and STOCK_LIST directly from .env file."""
    if not ENV_PATH.exists():
        pytest.skip(".env file not found")

    values = dotenv_values(ENV_PATH)
    token = (values.get("TUSHARE_TOKEN") or "").strip()
    stock_list_str = (values.get("STOCK_LIST") or "").strip()
    stock_list = [s.strip().upper() for s in stock_list_str.split(",") if s.strip()]
    return token, stock_list


def _is_us_code(code: str) -> bool:
    return bool(_US_PATTERN.match(code.strip().upper()))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def env_config():
    return _load_env_config()


@pytest.fixture(scope="module")
def tushare_token(env_config):
    token, _ = env_config
    if not token:
        pytest.skip("TUSHARE_TOKEN is empty or not configured in .env")
    return token


@pytest.fixture(scope="module")
def stock_list(env_config):
    _, stocks = env_config
    if not stocks:
        pytest.skip("STOCK_LIST is empty in .env")
    return stocks


@pytest.fixture(scope="module")
def tushare_api(tushare_token):
    """Initialize tushare pro_api and return (api_instance, token)."""
    try:
        import tushare as ts
    except ImportError:
        pytest.skip("tushare package not installed")

    ts.set_token(tushare_token)
    api = ts.pro_api()
    return api, tushare_token


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestTushareTokenValidity:
    """Verify TUSHARE_TOKEN is configured and the API is reachable."""

    def test_token_is_configured(self, tushare_token):
        assert len(tushare_token) > 10, "TUSHARE_TOKEN looks too short to be valid"
        logger.info("TUSHARE_TOKEN configured (first 8 chars: %s...)", tushare_token[:8])

    def test_api_initializes(self, tushare_api):
        api, _ = tushare_api
        assert api is not None, "tushare pro_api() returned None"
        logger.info("Tushare pro_api() initialized successfully")

    def test_api_reachable(self, tushare_api):
        """Call daily API with a known A-share code to confirm token is accepted."""
        api, token = tushare_api
        import requests
        import json

        url = "http://api.tushare.pro"
        end = datetime.now().strftime("%Y%m%d")
        start = (datetime.now() - timedelta(days=15)).strftime("%Y%m%d")
        payload = {
            "api_name": "daily",
            "token": token,
            "params": {"ts_code": "600519.SH", "start_date": start, "end_date": end},
            "fields": "",
        }
        resp = requests.post(url, json=payload, timeout=30)
        assert resp.status_code == 200, f"HTTP {resp.status_code}"
        body = json.loads(resp.text)
        assert body["code"] == 0, f"API error: {body.get('msg', 'unknown')}"
        items = body["data"]["items"]
        assert len(items) > 0, "daily(600519.SH) returned no data"
        logger.info("daily(600519.SH) returned %d rows — token is valid", len(items))


class TestStockListFetch:
    """For each stock in STOCK_LIST, attempt to fetch data via Tushare."""

    def test_stock_list_not_empty(self, stock_list):
        assert len(stock_list) > 0
        logger.info("STOCK_LIST has %d stocks: %s", len(stock_list), stock_list)

    def test_fetch_each_stock(self, tushare_api, stock_list):
        """
        Iterate over STOCK_LIST and try to fetch daily data from Tushare.

        - A-share / ETF codes: expected to succeed
        - US codes (e.g. NVDA, TSLA): Tushare does not support them, so we
          expect a graceful skip / failure and report it clearly.
        """
        api, token = tushare_api
        import requests
        import json

        url = "http://api.tushare.pro"
        end_date = datetime.now().strftime("%Y%m%d")
        start_date = (datetime.now() - timedelta(days=30)).strftime("%Y%m%d")

        results = []

        for code in stock_list:
            if _is_us_code(code):
                logger.warning(
                    "[%s] US stock detected — Tushare does NOT support US stocks. "
                    "Use YfinanceFetcher instead.",
                    code,
                )
                results.append((code, "SKIPPED_US", 0))
                continue

            ts_code = self._to_tushare_code(code)

            # Determine API name
            etf_sh = ("51", "52", "56", "58")
            etf_sz = ("15", "16", "18")
            pure = code.split(".")[0]
            is_etf = (pure.startswith(etf_sh) or pure.startswith(etf_sz)) and len(pure) == 6
            api_name = "fund_daily" if is_etf else "daily"

            payload = {
                "api_name": api_name,
                "token": token,
                "params": {
                    "ts_code": ts_code,
                    "start_date": start_date,
                    "end_date": end_date,
                },
                "fields": "",
            }

            try:
                resp = requests.post(url, json=payload, timeout=20)
                assert resp.status_code == 200, f"HTTP {resp.status_code}"
                body = json.loads(resp.text)

                if body["code"] != 0:
                    logger.error("[%s] API error: %s", code, body.get("msg"))
                    results.append((code, "API_ERROR", 0))
                    continue

                items = body["data"]["items"]
                columns = body["data"]["fields"]
                df = pd.DataFrame(items, columns=columns)
                row_count = len(df)

                if row_count > 0:
                    logger.info(
                        "[%s] (%s) fetched %d rows — latest: %s close=%.2f",
                        code,
                        ts_code,
                        row_count,
                        df.iloc[0].get("trade_date", "?"),
                        float(df.iloc[0].get("close", 0)),
                    )
                    results.append((code, "OK", row_count))
                else:
                    logger.warning("[%s] (%s) returned 0 rows", code, ts_code)
                    results.append((code, "EMPTY", 0))

            except Exception as exc:
                logger.error("[%s] fetch failed: %s", code, exc)
                results.append((code, "EXCEPTION", 0))

        # Print summary
        logger.info("=" * 60)
        logger.info("Fetch Summary:")
        ok_count = 0
        for code, status, rows in results:
            tag = {
                "OK": "SUCCESS",
                "EMPTY": "NO DATA",
                "SKIPPED_US": "SKIPPED (US stock, not supported by Tushare)",
                "API_ERROR": "FAILED (API error)",
                "EXCEPTION": "FAILED (exception)",
            }.get(status, status)
            logger.info("  %-10s => %s  (rows: %d)", code, tag, rows)
            if status == "OK":
                ok_count += 1

        # At least one stock should succeed, unless all are US stocks
        a_share_codes = [c for c in stock_list if not _is_us_code(c)]
        if a_share_codes:
            assert ok_count > 0, (
                f"None of the A-share stocks could be fetched: "
                f"{[r for r in results if r[1] != 'SKIPPED_US']}"
            )
        else:
            logger.warning(
                "All stocks in STOCK_LIST are US stocks. "
                "Tushare cannot fetch any of them. Consider adding A-share codes."
            )

    @staticmethod
    def _to_tushare_code(code: str) -> str:
        """Convert a bare stock code to Tushare ts_code format (e.g. 600519.SH)."""
        code = code.strip()
        if "." in code:
            return code.upper()
        if code.startswith(("600", "601", "603", "688")):
            return f"{code}.SH"
        if code.startswith(("51", "52", "56", "58")) and len(code) == 6:
            return f"{code}.SH"
        if code.startswith(("000", "002", "300")):
            return f"{code}.SZ"
        if code.startswith(("15", "16", "18")) and len(code) == 6:
            return f"{code}.SZ"
        return f"{code}.SZ"


class TestTushareFetcherIntegration:
    """
    Test using the project's own TushareFetcher class to ensure
    the full integration path works end-to-end.
    """

    @staticmethod
    def _import_fetcher():
        """Import TushareFetcher avoiding full package __init__ side-effects."""
        import importlib
        import importlib.util

        # Load tushare_fetcher module directly by file path to bypass
        # data_provider/__init__.py which pulls in all fetchers and
        # their transitive dependencies (e.g. fake_useragent).
        fetcher_path = Path(__file__).resolve().parent.parent / "data_provider" / "tushare_fetcher.py"
        spec = importlib.util.spec_from_file_location("data_provider.tushare_fetcher", str(fetcher_path))
        mod = importlib.util.module_from_spec(spec)

        # Ensure the base module is available for relative import
        base_path = fetcher_path.parent / "base.py"
        base_spec = importlib.util.spec_from_file_location("data_provider.base", str(base_path))
        base_mod = importlib.util.module_from_spec(base_spec)

        rt_path = fetcher_path.parent / "realtime_types.py"
        rt_spec = importlib.util.spec_from_file_location("data_provider.realtime_types", str(rt_path))
        rt_mod = importlib.util.module_from_spec(rt_spec)

        import sys as _sys
        _sys.modules.setdefault("data_provider", type(_sys)("data_provider"))
        _sys.modules.setdefault("data_provider.base", base_mod)
        _sys.modules.setdefault("data_provider.realtime_types", rt_mod)

        base_spec.loader.exec_module(base_mod)
        rt_spec.loader.exec_module(rt_mod)
        spec.loader.exec_module(mod)
        return mod.TushareFetcher

    def test_fetcher_initialization(self, tushare_token):
        """TushareFetcher should initialize with a valid token."""
        os.environ["TUSHARE_TOKEN"] = tushare_token

        from src.config import Config
        Config.reset_instance()

        TushareFetcher = self._import_fetcher()
        fetcher = TushareFetcher()

        assert fetcher.is_available(), "TushareFetcher reports unavailable despite valid token"
        logger.info(
            "TushareFetcher initialized OK — priority=%d, available=%s",
            fetcher.priority,
            fetcher.is_available(),
        )

    def test_fetcher_get_daily_data(self, tushare_token, stock_list):
        """TushareFetcher.get_daily_data for each compatible stock."""
        os.environ["TUSHARE_TOKEN"] = tushare_token

        from src.config import Config
        Config.reset_instance()

        TushareFetcher = self._import_fetcher()
        fetcher = TushareFetcher()

        if not fetcher.is_available():
            pytest.skip("TushareFetcher not available")

        for code in stock_list:
            if _is_us_code(code):
                logger.info("[%s] Skipping US stock in TushareFetcher test", code)
                continue

            try:
                df = fetcher.get_daily_data(code, days=10)
                assert df is not None and not df.empty, f"No data returned for {code}"
                assert "close" in df.columns, f"Missing 'close' column for {code}"
                logger.info(
                    "[%s] TushareFetcher returned %d rows, latest close=%.2f",
                    code,
                    len(df),
                    df.iloc[-1]["close"],
                )
            except Exception as exc:
                logger.error("[%s] TushareFetcher.get_daily_data failed: %s", code, exc)
                raise


# ---------------------------------------------------------------------------
# YfinanceFetcher tests for US stocks
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def us_stock_list(stock_list):
    """Filter STOCK_LIST to US stocks only."""
    us = [c for c in stock_list if _is_us_code(c)]
    if not us:
        pytest.skip("No US stocks in STOCK_LIST")
    return us


class TestYfinanceFetchUSStocks:
    """Fetch US stocks from STOCK_LIST using YfinanceFetcher (yfinance)."""

    @staticmethod
    def _import_yfinance_fetcher():
        """Import YfinanceFetcher avoiding full package __init__ side-effects."""
        import importlib.util

        dp_dir = Path(__file__).resolve().parent.parent / "data_provider"

        base_spec = importlib.util.spec_from_file_location("data_provider.base", str(dp_dir / "base.py"))
        base_mod = importlib.util.module_from_spec(base_spec)

        rt_spec = importlib.util.spec_from_file_location("data_provider.realtime_types", str(dp_dir / "realtime_types.py"))
        rt_mod = importlib.util.module_from_spec(rt_spec)

        us_map_spec = importlib.util.spec_from_file_location("data_provider.us_index_mapping", str(dp_dir / "us_index_mapping.py"))
        us_map_mod = importlib.util.module_from_spec(us_map_spec)

        sys.modules.setdefault("data_provider", type(sys)("data_provider"))
        sys.modules.setdefault("data_provider.base", base_mod)
        sys.modules.setdefault("data_provider.realtime_types", rt_mod)
        sys.modules.setdefault("data_provider.us_index_mapping", us_map_mod)

        base_spec.loader.exec_module(base_mod)
        rt_spec.loader.exec_module(rt_mod)
        us_map_spec.loader.exec_module(us_map_mod)

        yf_spec = importlib.util.spec_from_file_location("data_provider.yfinance_fetcher", str(dp_dir / "yfinance_fetcher.py"))
        yf_mod = importlib.util.module_from_spec(yf_spec)
        yf_spec.loader.exec_module(yf_mod)
        return yf_mod.YfinanceFetcher

    def test_yfinance_installed(self):
        """Verify yfinance package is available."""
        try:
            import yfinance  # noqa: F401
        except ImportError:
            pytest.fail("yfinance package is not installed — required for US stock data")
        logger.info("yfinance package version: %s", yfinance.__version__)

    def test_yfinance_ticker_history(self, us_stock_list):
        """Use yfinance Ticker.history() for each US stock to verify network + data."""
        import yfinance as yf

        results = []
        for code in us_stock_list:
            try:
                ticker = yf.Ticker(code)
                hist = ticker.history(period="1mo")
                row_count = len(hist)
                if row_count > 0:
                    latest_close = float(hist["Close"].iloc[-1])
                    latest_date = str(hist.index[-1].date())
                    logger.info(
                        "[%s] Ticker.history OK — %d rows, latest: %s close=%.2f",
                        code, row_count, latest_date, latest_close,
                    )
                    results.append((code, "OK", row_count, latest_close))
                else:
                    logger.warning("[%s] Ticker.history returned 0 rows", code)
                    results.append((code, "EMPTY", 0, 0.0))
            except Exception as exc:
                logger.error("[%s] Ticker.history failed: %s", code, exc)
                results.append((code, "EXCEPTION", 0, 0.0))

        # Summary
        logger.info("=" * 60)
        logger.info("YFinance Ticker.history Summary:")
        ok_count = 0
        for code, status, rows, close in results:
            if status == "OK":
                logger.info("  %-6s => SUCCESS  %d rows, close=%.2f", code, rows, close)
                ok_count += 1
            else:
                logger.info("  %-6s => %s", code, status)

        assert ok_count > 0, f"None of the US stocks could be fetched via yfinance: {results}"

    def test_yfinance_fetcher_integration(self, us_stock_list):
        """Test using the project's YfinanceFetcher.get_daily_data() end-to-end."""
        from src.config import Config
        Config.reset_instance()

        YfinanceFetcher = self._import_yfinance_fetcher()
        fetcher = YfinanceFetcher()

        for code in us_stock_list:
            try:
                df = fetcher.get_daily_data(code, days=10)
                assert df is not None and not df.empty, f"No data returned for {code}"
                assert "close" in df.columns, f"Missing 'close' column for {code}"
                latest_close = float(df.iloc[-1]["close"])
                logger.info(
                    "[%s] YfinanceFetcher.get_daily_data OK — %d rows, latest close=%.2f",
                    code, len(df), latest_close,
                )
            except Exception as exc:
                logger.error("[%s] YfinanceFetcher.get_daily_data failed: %s", code, exc)
                raise

    def test_yfinance_fetcher_realtime_quote(self, us_stock_list):
        """Test YfinanceFetcher.get_realtime_quote() for US stocks."""
        from src.config import Config
        Config.reset_instance()

        YfinanceFetcher = self._import_yfinance_fetcher()
        fetcher = YfinanceFetcher()

        for code in us_stock_list:
            try:
                quote = fetcher.get_realtime_quote(code)
                if quote is not None:
                    logger.info(
                        "[%s] realtime quote: price=%.2f, change_pct=%s, name=%s",
                        code,
                        quote.price or 0,
                        quote.change_pct,
                        quote.name,
                    )
                    assert quote.price is not None and quote.price > 0, f"Invalid price for {code}"
                else:
                    logger.warning("[%s] get_realtime_quote returned None (market may be closed)", code)
            except Exception as exc:
                logger.error("[%s] get_realtime_quote failed: %s", code, exc)
                raise


# ---------------------------------------------------------------------------
# Standalone runner
# ---------------------------------------------------------------------------

def _standalone_run():
    """Run tests without pytest for quick manual verification."""
    print("\n" + "=" * 60)
    print("  Tushare & YFinance Stock Fetch Test")
    print("=" * 60)

    token, stock_list = _load_env_config()

    print(f"\n[Config]")
    print(f"  .env path   : {ENV_PATH}")
    print(f"  TUSHARE_TOKEN: {'configured (' + token[:8] + '...)' if token else 'NOT SET'}")
    print(f"  STOCK_LIST   : {stock_list}")

    if not token:
        print("\n[FAIL] TUSHARE_TOKEN is not configured. Cannot proceed.")
        return 1

    # Test API connectivity using daily() which has lower permission requirements
    print(f"\n[1/3] Testing API connectivity (daily API with 600519.SH)...")
    import requests
    import json

    url = "http://api.tushare.pro"
    end_dt = datetime.now().strftime("%Y%m%d")
    start_dt = (datetime.now() - timedelta(days=15)).strftime("%Y%m%d")
    payload = {
        "api_name": "daily",
        "token": token,
        "params": {"ts_code": "600519.SH", "start_date": start_dt, "end_date": end_dt},
        "fields": "",
    }
    try:
        resp = requests.post(url, json=payload, timeout=30)
        body = json.loads(resp.text)
        if body["code"] == 0 and body["data"] and body["data"]["items"]:
            items = body["data"]["items"]
            print(f"  OK — daily(600519.SH) returned {len(items)} rows, token is valid")
        else:
            msg = body.get("msg", "no data returned")
            print(f"  FAIL — API error: {msg}")
            return 1
    except Exception as e:
        print(f"  FAIL — {e}")
        return 1

    # Test fetching each stock
    print(f"\n[2/3] Fetching stock data for STOCK_LIST...")
    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=30)).strftime("%Y%m%d")

    all_ok = True
    for code in stock_list:
        if _is_us_code(code):
            print(f"  [{code}] SKIPPED — US stock, Tushare does not support it")
            continue

        # Convert code
        c = code.strip()
        if c.startswith(("600", "601", "603", "688")):
            ts_code = f"{c}.SH"
        elif c.startswith(("000", "002", "300")):
            ts_code = f"{c}.SZ"
        else:
            ts_code = f"{c}.SZ"

        payload = {
            "api_name": "daily",
            "token": token,
            "params": {"ts_code": ts_code, "start_date": start_date, "end_date": end_date},
            "fields": "",
        }
        try:
            resp = requests.post(url, json=payload, timeout=20)
            body = json.loads(resp.text)
            if body["code"] != 0:
                print(f"  [{code}] FAIL — API error: {body.get('msg')}")
                all_ok = False
                continue
            items = body["data"]["items"]
            columns = body["data"]["fields"]
            df = pd.DataFrame(items, columns=columns)
            if len(df) > 0:
                latest = df.iloc[0]
                print(
                    f"  [{code}] OK — {len(df)} rows, "
                    f"latest: {latest.get('trade_date', '?')} "
                    f"close={float(latest.get('close', 0)):.2f}"
                )
            else:
                print(f"  [{code}] WARNING — 0 rows returned")
        except Exception as e:
            print(f"  [{code}] FAIL — {e}")
            all_ok = False

    # Test TushareFetcher integration
    print(f"\n[3/3] Testing TushareFetcher integration...")
    os.environ["TUSHARE_TOKEN"] = token
    try:
        from src.config import Config
        Config.reset_instance()

        import importlib.util

        fetcher_path = ENV_PATH.parent / "data_provider" / "tushare_fetcher.py"
        base_path = fetcher_path.parent / "base.py"
        rt_path = fetcher_path.parent / "realtime_types.py"

        base_spec = importlib.util.spec_from_file_location("data_provider.base", str(base_path))
        base_mod = importlib.util.module_from_spec(base_spec)
        rt_spec = importlib.util.spec_from_file_location("data_provider.realtime_types", str(rt_path))
        rt_mod = importlib.util.module_from_spec(rt_spec)

        sys.modules.setdefault("data_provider", type(sys)("data_provider"))
        sys.modules.setdefault("data_provider.base", base_mod)
        sys.modules.setdefault("data_provider.realtime_types", rt_mod)

        base_spec.loader.exec_module(base_mod)
        rt_spec.loader.exec_module(rt_mod)

        spec = importlib.util.spec_from_file_location("data_provider.tushare_fetcher", str(fetcher_path))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        TushareFetcher = mod.TushareFetcher
        fetcher = TushareFetcher()
        status = "available" if fetcher.is_available() else "NOT available"
        print(f"  TushareFetcher: {status} (priority={fetcher.priority})")

        if fetcher.is_available():
            a_share = [c for c in stock_list if not _is_us_code(c)]
            if a_share:
                test_code = a_share[0]
                df = fetcher.get_daily_data(test_code, days=5)
                print(f"  get_daily_data('{test_code}'): {len(df)} rows")
            else:
                print("  No A-share stocks in STOCK_LIST to test with TushareFetcher")
    except Exception as e:
        print(f"  TushareFetcher integration error: {e}")

    # Test US stocks with YfinanceFetcher
    us_stocks = [c for c in stock_list if _is_us_code(c)]
    a_shares = [c for c in stock_list if not _is_us_code(c)]

    if us_stocks:
        print(f"\n[4/5] Fetching US stocks via yfinance: {us_stocks}")
        try:
            import yfinance as yf
            print(f"  yfinance version: {yf.__version__}")

            end_d = datetime.now().strftime("%Y-%m-%d")
            start_d = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")

            for code in us_stocks:
                try:
                    df = yf.download(code, start=start_d, end=end_d, progress=False, auto_adjust=True)
                    if len(df) > 0:
                        close_col = df["Close"]
                        if isinstance(close_col, pd.DataFrame):
                            close_col = close_col.iloc[:, 0]
                        latest_close = float(close_col.iloc[-1])
                        latest_date = str(df.index[-1].date())
                        print(f"  [{code}] OK — {len(df)} rows, latest: {latest_date} close={latest_close:.2f}")
                    else:
                        print(f"  [{code}] WARNING — 0 rows returned")
                except Exception as e:
                    print(f"  [{code}] FAIL — {e}")
                    all_ok = False
        except ImportError:
            print("  FAIL — yfinance package not installed")
            all_ok = False

        print(f"\n[5/5] Testing YfinanceFetcher integration...")
        try:
            from src.config import Config
            Config.reset_instance()

            import importlib.util
            dp_dir = ENV_PATH.parent / "data_provider"

            base_spec = importlib.util.spec_from_file_location("data_provider.base", str(dp_dir / "base.py"))
            base_mod = importlib.util.module_from_spec(base_spec)
            rt_spec = importlib.util.spec_from_file_location("data_provider.realtime_types", str(dp_dir / "realtime_types.py"))
            rt_mod = importlib.util.module_from_spec(rt_spec)
            us_map_spec = importlib.util.spec_from_file_location("data_provider.us_index_mapping", str(dp_dir / "us_index_mapping.py"))
            us_map_mod = importlib.util.module_from_spec(us_map_spec)

            sys.modules.setdefault("data_provider.us_index_mapping", us_map_mod)

            base_spec.loader.exec_module(base_mod)
            rt_spec.loader.exec_module(rt_mod)
            us_map_spec.loader.exec_module(us_map_mod)

            yf_spec = importlib.util.spec_from_file_location("data_provider.yfinance_fetcher", str(dp_dir / "yfinance_fetcher.py"))
            yf_mod = importlib.util.module_from_spec(yf_spec)
            yf_spec.loader.exec_module(yf_mod)
            fetcher = yf_mod.YfinanceFetcher()

            for code in us_stocks:
                try:
                    df = fetcher.get_daily_data(code, days=5)
                    print(f"  [{code}] YfinanceFetcher.get_daily_data: {len(df)} rows, close={float(df.iloc[-1]['close']):.2f}")
                except Exception as e:
                    print(f"  [{code}] YfinanceFetcher FAIL — {e}")
                    all_ok = False
        except Exception as e:
            print(f"  YfinanceFetcher integration error: {e}")
    else:
        print(f"\n[4/5] No US stocks in STOCK_LIST, skipping yfinance tests")

    # Summary
    print(f"\n{'=' * 60}")
    print(f"  A-share stocks: {a_shares if a_shares else '(none)'}")
    print(f"  US stocks     : {us_stocks if us_stocks else '(none)'}")
    if a_shares:
        print(f"  A-share fetch : via Tushare (TUSHARE_TOKEN)")
    if us_stocks:
        print(f"  US fetch      : via YfinanceFetcher (yfinance)")
    print(f"{'=' * 60}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(_standalone_run())
