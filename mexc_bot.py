#!/usr/bin/env python3
"""
MEXC Cryptocurrency Price Monitor with Telegram Alerts
Enhanced with pump/dump and volume spike detection
Monitors top gainers and losers on MEXC exchange and sends alerts via Telegram
"""

import requests
import time
import os
import json
from datetime import datetime, timedelta
from collections import deque, defaultdict
from requests.exceptions import RequestException, Timeout

# Telegram credentials (using environment variables for security)
BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID", "YOUR_CHAT_ID")

# MEXC API URLs
FUTURES_TICKER_URL = "https://contract.mexc.com/api/v1/contract/ticker"
FUTURES_DETAIL_URL = "https://contract.mexc.com/api/v1/contract/detail"
SPOT_EXCHANGE_INFO_URL = "https://api.mexc.com/api/v3/exchangeInfo"

# Price history storage - tracks 1-hour rolling window for each symbol
price_history = defaultdict(lambda: deque(maxlen=75))  # Store ~375 minutes of data (1 sample per 5 minutes)
alert_cooldowns = defaultdict(lambda: defaultdict(float))  # Track last alert time per symbol per type

# New listing tracking
known_spot_symbols = set()  # Track known spot trading pairs
known_futures_symbols = set()  # Track known futures contracts
listing_initialized = False  # Flag to skip first-run false positives

# Detection thresholds
PUMP_THRESHOLD = 8.0  # 8% increase in 1 hour = pump
DUMP_THRESHOLD = -8.0  # 8% decrease in 1 hour = dump
ALERT_COOLDOWN_MINUTES = 15  # Minimum minutes between same alert type for same symbol
ALERT_INTERVAL_SECONDS = 300  # Send alerts every 5 minutes (300 seconds)

def send_telegram_message(message: str) -> bool:
    """
    Send a message via Telegram bot
    Returns True if successful, False otherwise
    """
    if BOT_TOKEN == "YOUR_TELEGRAM_BOT_TOKEN" or CHAT_ID == "YOUR_CHAT_ID":
        print("⚠️  Telegram credentials not configured. Set BOT_TOKEN and CHAT_ID environment variables.")
        return False

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID, 
        "text": message,
        "parse_mode": "HTML"  # Enable HTML formatting
    }

    try:
        response = requests.post(url, data=payload, timeout=10)

        if response.status_code == 200:
            result = response.json()
            if result.get("ok"):
                return True
            else:
                print(f"❌ Telegram API error: {result.get('description', 'Unknown error')}")
                return False
        else:
            # Get more detailed error info
            try:
                error_data = response.json()
                print(f"❌ Telegram HTTP {response.status_code}: {error_data.get('description', 'Unknown error')}")

                # Common error suggestions
                if response.status_code == 400:
                    print("💡 Common causes for 400 error:")
                    print("   - CHAT_ID format incorrect (should be a number like 123456789)")
                    print("   - Bot token format incorrect")
                    print("   - You haven't started a chat with your bot yet")
                    print(f"   Current CHAT_ID format: {type(CHAT_ID).__name__}")
            except:
                print(f"❌ Telegram HTTP {response.status_code}: {response.text}")
            return False

    except RequestException as e:
        print(f"❌ Telegram connection error: {e}")
        return False

def fetch_spot_symbols() -> set:
    """
    Fetch current spot trading pairs from MEXC API
    Returns set of symbol names
    """
    try:
        response = requests.get(SPOT_EXCHANGE_INFO_URL, timeout=10)
        response.raise_for_status()
        data = response.json()

        spot_symbols = set()
        for symbol_info in data.get('symbols', []):
            status = symbol_info.get('status')
            if status in {'1', 1, 'ENABLED', 'TRADING'} and symbol_info.get('isSpotTradingAllowed', False):
                spot_symbols.add(symbol_info['symbol'])

        return spot_symbols
    except Exception as e:
        print(f"❌ Error fetching spot symbols: {e}")
        return set()

def fetch_futures_symbols() -> set:
    """
    Fetch current futures contract symbols from MEXC API
    Returns set of symbol names
    """
    try:
        response = requests.get(FUTURES_DETAIL_URL, timeout=10)
        response.raise_for_status()
        data = response.json()

        futures_symbols = set()
        if isinstance(data, dict) and data.get('success') and 'data' in data:
            for contract in data['data']:
                if isinstance(contract, dict) and 'symbol' in contract:
                    futures_symbols.add(contract['symbol'])
        elif isinstance(data, list):
            for contract in data:
                if isinstance(contract, dict) and 'symbol' in contract:
                    futures_symbols.add(contract['symbol'])

        return futures_symbols
    except Exception as e:
        print(f"❌ Error fetching futures symbols: {e}")
        return set()

def format_new_listing_alert(symbol: str, market_type: str) -> str:
    """
    Format new listing alert message for Telegram
    """
    emoji = "🆕" if market_type == "SPOT" else "🚀"
    market_label = "Spot" if market_type == "SPOT" else "Futures"

    msg = f"{emoji} <b>NEW {market_type} LISTING!</b>\n"
    msg += f"💰 <code>{symbol}</code>\n"
    msg += f"📈 Now available for {market_label} trading\n"
    msg += f"⏰ {datetime.now().strftime('%H:%M:%S')}"

    return msg

def check_new_listings() -> list:
    """
    Check for new spot and futures listings
    Returns list of alert messages for new listings
    """
    global known_spot_symbols, known_futures_symbols, listing_initialized

    alerts = []

    try:
        # Fetch current symbols
        current_spot_symbols = fetch_spot_symbols()
        current_futures_symbols = fetch_futures_symbols()

        # Skip first run to avoid false positives - only initialize if we get reasonable data
        if not listing_initialized:
            # Only initialize if we get reasonable amounts of data (avoid empty API responses)
            if len(current_spot_symbols) > 1000 and len(current_futures_symbols) > 500:
                known_spot_symbols = current_spot_symbols.copy()
                known_futures_symbols = current_futures_symbols.copy()
                listing_initialized = True
                print(f"📊 Initialized tracking: {len(known_spot_symbols)} spot pairs, {len(known_futures_symbols)} futures contracts")
            else:
                print(f"⚠️  Insufficient data for initialization: {len(current_spot_symbols)} spot, {len(current_futures_symbols)} futures. Retrying next cycle...")
            return alerts

        # Check for new spot listings
        new_spot_symbols = current_spot_symbols - known_spot_symbols
        if new_spot_symbols:
            for symbol in sorted(new_spot_symbols):
                alert_msg = format_new_listing_alert(symbol, "SPOT")
                alerts.append(alert_msg)
                print(f"🆕 New spot listing detected: {symbol}")
            known_spot_symbols.update(new_spot_symbols)

        # Check for new futures listings
        new_futures_symbols = current_futures_symbols - known_futures_symbols
        if new_futures_symbols:
            for symbol in sorted(new_futures_symbols):
                alert_msg = format_new_listing_alert(symbol, "FUTURES")
                alerts.append(alert_msg)
                print(f"🚀 New futures listing detected: {symbol}")
            known_futures_symbols.update(new_futures_symbols)

        # Log status periodically
        if len(alerts) == 0:
            print(f"📊 Monitoring {len(current_spot_symbols)} spot pairs, {len(current_futures_symbols)} futures contracts")

    except Exception as e:
        print(f"❌ Error checking new listings: {e}")

    return alerts

def update_price_history(all_tickers: list):
    """Update price history with current futures price data"""
    current_time = datetime.now()

    for ticker in all_tickers:
        try:
            symbol = ticker.get('symbol', '')
            if symbol.endswith('_USDT'):  # Only track USDT futures pairs
                last_price = float(ticker.get('lastPrice', 0))
                if last_price > 0:
                    # Add current price and timestamp to history
                    price_history[symbol].append((current_time, last_price))
        except (ValueError, TypeError):
            continue

def calculate_1hour_change(symbol: str, current_price: float) -> float | None:
    """Calculate 1-hour price change percentage for a symbol"""
    if symbol not in price_history or len(price_history[symbol]) < 2:
        return None

    current_time = datetime.now()
    target_time = current_time - timedelta(hours=1)

    # Find the price closest to 1 hour ago
    history = price_history[symbol]
    baseline_price = None

    # Look for price data around 1 hour ago
    for timestamp, price in history:
        if timestamp <= target_time:
            baseline_price = price
        else:
            break

    if baseline_price is None or baseline_price <= 0:
        return None

    # Calculate percentage change
    change_percent = ((current_price - baseline_price) / baseline_price) * 100
    return change_percent

def detect_pump_dump(symbol: str, current_price: float) -> dict | None:
    """
    Detect sudden pumps and dumps based on 1-hour price changes
    Returns dict with alert info if pump/dump detected, None otherwise
    """
    if symbol not in price_history or len(price_history[symbol]) < 12:
        return None

    current_time = datetime.now()
    target_time = current_time - timedelta(hours=1)

    # Find the price closest to 1 hour ago
    history = price_history[symbol]
    baseline_price = None

    for timestamp, price in history:
        if timestamp <= target_time:
            baseline_price = price
        else:
            break

    if baseline_price is None or baseline_price <= 0:
        return None

    # Calculate 1-hour percentage change
    change_percent = ((current_price - baseline_price) / baseline_price) * 100

    # Check for pump or dump
    if change_percent >= PUMP_THRESHOLD:
        return {
            'type': 'PUMP',
            'symbol': symbol.replace('_USDT', ''),
            'change': change_percent,
            'price': current_price,
            'timeframe': '1hour'
        }
    elif change_percent <= DUMP_THRESHOLD:
        return {
            'type': 'DUMP',
            'symbol': symbol.replace('_USDT', ''),
            'change': change_percent,
            'price': current_price,
            'timeframe': '1hour'
        }

    return None


def format_pump_dump_alert(alert: dict) -> str:
    """Format pump/dump alert message for Telegram"""
    emoji = "🚀" if alert['type'] == 'PUMP' else "💥"
    symbol = alert['symbol']
    change = alert['change']
    price = f"{alert['price']:.6f}".rstrip('0').rstrip('.')

    msg = f"{emoji} <b>{alert['type']} ALERT!</b>\n"
    msg += f"💰 <code>{symbol}</code>\n"
    msg += f"📈 <b>{change:+.2f}%</b> in {alert['timeframe']}\n"
    msg += f"💵 Price: ${price}\n"
    msg += f"⏰ {datetime.now().strftime('%H:%M:%S')}"

    return msg


def is_alert_on_cooldown(symbol: str, alert_type: str) -> bool:
    """Check if an alert type for a symbol is still on cooldown"""
    current_time = datetime.now().timestamp()
    last_alert_time = alert_cooldowns[symbol].get(alert_type, 0)
    cooldown_seconds = ALERT_COOLDOWN_MINUTES * 60
    return (current_time - last_alert_time) < cooldown_seconds

def set_alert_cooldown(symbol: str, alert_type: str):
    """Set cooldown for an alert type on a symbol"""
    alert_cooldowns[symbol][alert_type] = datetime.now().timestamp()

def check_alerts(all_tickers: list) -> list:
    """
    Check for pump/dump and volume spike alerts with cooldown protection
    Returns list of alert messages
    """
    alerts = []

    for ticker in all_tickers:
        try:
            symbol = ticker.get('symbol', '')
            if not symbol.endswith('_USDT'):
                continue

            current_price = float(ticker.get('lastPrice', 0))
            current_volume = float(ticker.get('volume24', 0))

            if current_price <= 0 or current_volume <= 0:
                continue

            # Check for pump/dump with cooldown
            pump_dump_alert = detect_pump_dump(symbol, current_price)
            if pump_dump_alert and not is_alert_on_cooldown(symbol, pump_dump_alert['type']):
                alert_msg = format_pump_dump_alert(pump_dump_alert)
                alerts.append(alert_msg)
                set_alert_cooldown(symbol, pump_dump_alert['type'])
                print(f"🚨 {pump_dump_alert['type']} detected: {pump_dump_alert['symbol']} {pump_dump_alert['change']:+.2f}%")


        except (ValueError, TypeError):
            continue

    return alerts

def get_top_gainers_and_losers() -> str | None:
    """
    Fetch current ticker data from MEXC API and calculate 15-minute price changes
    Returns formatted message string or None if error occurs
    """
    try:
        print(f"🔄 Fetching futures ticker data from MEXC API at {datetime.now().strftime('%H:%M:%S')}")

        # Make API request to get all futures ticker data
        response = requests.get(FUTURES_TICKER_URL, timeout=15)
        response.raise_for_status()

        all_tickers = response.json()

        # Handle futures API response format
        if isinstance(all_tickers, dict):
            if all_tickers.get('success') and 'data' in all_tickers:
                all_tickers = all_tickers['data']
            else:
                print("❌ Invalid futures API response format")
                return None

        if not isinstance(all_tickers, list) or len(all_tickers) == 0:
            print("❌ No futures data received from API")
            return None

        print(f"📊 Processing {len(all_tickers)} trading pairs")

        # Update price history with current data
        update_price_history(all_tickers)

        # Check for pump/dump and volume spike alerts
        alerts = check_alerts(all_tickers)

        # Check for new listings
        listing_alerts = check_new_listings()

        # Send individual alerts immediately if found
        for alert in alerts + listing_alerts:
            send_telegram_message(alert)

        # Filter and calculate 15-minute changes for futures pairs
        valid_tickers = []
        symbols_with_15m_data = 0

        for ticker in all_tickers:
            try:
                symbol = ticker.get('symbol', '')
                current_price = float(ticker.get('lastPrice', 0))
                volume24 = float(ticker.get('volume24', 0))

                # Filter criteria: USDT futures pairs with meaningful volume
                if (symbol.endswith('_USDT') and 
                    current_price > 0 and
                    volume24 > 100):  # Minimum volume for futures

                    # Calculate 1-hour change
                    change_1h = calculate_1hour_change(symbol, current_price)

                    if change_1h is not None:
                        valid_tickers.append({
                            'symbol': symbol,
                            'priceChangePercent': change_1h,
                            'volume24': volume24,
                            'lastPrice': current_price
                        })
                        symbols_with_15m_data += 1

            except (ValueError, TypeError):
                continue

        print(f"📈 Calculated 1-hour changes for {symbols_with_15m_data} symbols")

        if len(valid_tickers) < 6:
            print("⏳ Insufficient 1-hour data (need ~60 minutes of runtime for accurate results)")
            if symbols_with_15m_data == 0:
                return None

        # Sort by 1-hour percentage change
        gainers = sorted(valid_tickers, key=lambda x: x['priceChangePercent'], reverse=True)[:10]  # Top 10 gainers
        losers = sorted(valid_tickers, key=lambda x: x['priceChangePercent'])[:10]  # Top 10 losers

        # Build formatted message
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        msg = f"📊 <b>MEXC 1-Hour Movers (Futures)</b>\n"
        msg += f"🕐 {timestamp}\n\n"

        msg += "🚀 <b>Top 1-Hour Gainers:</b>\n"
        for i, coin in enumerate(gainers[:10], 1):  # Show top 10
            symbol = coin['symbol'].replace('_USDT', '')  # Remove _USDT suffix for cleaner display
            change_percent = f"{coin['priceChangePercent']:.2f}"
            price = f"{coin['lastPrice']:.6f}".rstrip('0').rstrip('.')
            msg += f"{i}. <code>{symbol}</code>: <b>+{change_percent}%</b> (${price})\n"

        msg += "\n📉 <b>Top 1-Hour Losers:</b>\n"
        for i, coin in enumerate(losers[:10], 1):  # Show top 10
            symbol = coin['symbol'].replace('_USDT', '')  # Remove _USDT suffix for cleaner display
            change_percent = f"{coin['priceChangePercent']:.2f}"
            price = f"{coin['lastPrice']:.6f}".rstrip('0').rstrip('.')
            msg += f"{i}. <code>{symbol}</code>: <b>{change_percent}%</b> (${price})\n"

        pump_dump_alerts = len(alerts)
        listing_alert_count = len(listing_alerts)
        total_alert_count = pump_dump_alerts + listing_alert_count

        msg += f"\n💡 <i>Updated every 5 minutes • {symbols_with_15m_data} futures with 1-hour data</i>"
        if total_alert_count > 0:
            alert_details = []
            if pump_dump_alerts > 0:
                alert_details.append(f"{pump_dump_alerts} pump/dump")
            if listing_alert_count > 0:
                alert_details.append(f"{listing_alert_count} new listing")
            msg += f"\n🚨 <i>{' + '.join(alert_details)} alerts sent separately</i>"

        print(f"✅ 1-hour analysis complete: {len(gainers)} gainers, {len(losers)} losers")
        if total_alert_count > 0:
            print(f"🚨 {total_alert_count} special alerts detected and sent ({pump_dump_alerts} pump/dump, {listing_alert_count} new listings)")

        return msg

    except RequestException as e:
        print(f"❌ API request error: {e}")
        return None
    except json.JSONDecodeError as e:
        print(f"❌ JSON parsing error: {e}")
        return None
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        return None

def test_telegram_connection():
    """Test if Telegram bot credentials are working"""
    print("🔧 Testing Telegram connection...")
    test_msg = "🤖 Enhanced MEXC Price Monitor Bot is online!\n💡 Features: 1-hour movers, pump/dump detection, new listing alerts"
    if send_telegram_message(test_msg):
        print("✅ Telegram connection successful")
        return True
    else:
        print("❌ Telegram connection failed")
        return False

def main():
    """Main monitoring loop"""
    print("🚀 Enhanced MEXC Cryptocurrency Price Monitor Starting...")
    print("💡 Features: 1-hour movers + pump/dump detection + new listing alerts")
    print("=" * 60)

    # Test connections first
    telegram_working = test_telegram_connection()

    if not telegram_working:
        print("\n⚠️  Running in demo mode (no Telegram alerts)")
        print("To enable Telegram alerts:")
        print("1. Create a Telegram bot via @BotFather")
        print("2. Set BOT_TOKEN environment variable")
        print("3. Set CHAT_ID environment variable")
        print()

    # Test MEXC Futures API connection
    print("🔄 Testing MEXC Futures API connection...")
    try:
        response = requests.get(FUTURES_TICKER_URL, timeout=10)
        response.raise_for_status()
        data = response.json()

        # Handle futures API response format
        if isinstance(data, dict) and data.get('success') and 'data' in data:
            futures_data = data['data']
            if isinstance(futures_data, list) and len(futures_data) > 0:
                print("✅ MEXC Futures API connection successful")
                print(f"📊 Found {len(futures_data)} futures pairs available")
            else:
                print("❌ No futures data available")
                return
        elif isinstance(data, list) and len(data) > 0:
            print("✅ MEXC Futures API connection successful")
            print(f"📊 Found {len(data)} futures pairs available")
        else:
            print("❌ Invalid futures API response")
            return
    except Exception as e:
        print(f"❌ Failed to connect to MEXC Futures API: {e}")
        return

    print("⏳ Starting price history collection...")
    print("💡 1-hour alerts will begin after ~60 minutes of data collection")
    print("🚨 Pump/dump alerts (8%+ in 1hour) start after ~60 minutes")
    print("🆕 New listing alerts (spot & futures) start immediately")
    print()

    # Main monitoring loop
    print("🔄 Starting monitoring loop (every 5 minutes)")
    print("Press Ctrl+C to stop")
    print("=" * 60)

    last_update_time = 0
    consecutive_errors = 0

    try:
        while True:
            current_time = time.time()

            # Check if enough time has passed (every 5 minutes)
            if current_time - last_update_time >= ALERT_INTERVAL_SECONDS:
                try:
                    # Get top movers and send alert if Telegram is working
                    message = get_top_gainers_and_losers()

                    if message and telegram_working:
                        if send_telegram_message(message):
                            print("✅ Update sent to Telegram successfully")
                        else:
                            print("⚠️  Failed to send update to Telegram")

                    last_update_time = current_time
                    consecutive_errors = 0  # Reset error counter on success

                except Exception as e:
                    consecutive_errors += 1
                    print(f"❌ Error in monitoring cycle #{consecutive_errors}: {e}")

                    # If too many consecutive errors, wait longer
                    if consecutive_errors >= 3:
                        print("⚠️  Multiple consecutive errors detected. Waiting 60 seconds before retry...")
                        time.sleep(60)
                        consecutive_errors = 0  # Reset after extended wait

            # Short sleep to prevent excessive CPU usage
            time.sleep(30)  # Check every 30 seconds

    except KeyboardInterrupt:
        print("\n🛑 Monitoring stopped by user")
        print("👋 MEXC Price Monitor terminated gracefully")

if __name__ == "__main__":
    main()