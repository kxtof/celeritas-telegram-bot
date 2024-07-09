import datetime
import time
import requests
from solana.rpc.api import Client
from solders.hash import Hash


async def delete_messages(context, chat_id, *message_ids):
    for message_id in message_ids:
        if message_id:
            await context.bot.delete_message(chat_id=chat_id, message_id=message_id)


async def edit_message(context, chat_id, message_id, text, reply_markup):
    if message_id:
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            reply_markup=reply_markup,
            parse_mode='HTML',
            disable_web_page_preview=True
        )


def utc_time_now():
    return str(datetime.datetime.utcnow().strftime("%H:%M:%S.%f"))[:-5]


def center_arrow(top_line, bottom_line, arrow="⇅", width=34):
    # Find the position of '|' in both lines
    top_pipe_index = top_line.index('|')
    bottom_pipe_index = bottom_line.index('|')
    # Calculate the maximum length before and after the '|'
    max_left = max(top_pipe_index, bottom_pipe_index)
    max_right = max(len(top_line) - top_pipe_index, len(bottom_line) - bottom_pipe_index)
    # Format both lines to align the '|'
    formatted_top = f"{top_line[:top_pipe_index].rjust(max_left)}{top_line[top_pipe_index:].ljust(max_right)}"
    formatted_bottom = f"{bottom_line[:bottom_pipe_index].rjust(max_left)}{bottom_line[bottom_pipe_index:].ljust(max_right)}"
    # Calculate total width and center position
    total_width = max_left + max_right
    center_position = max_left    
    # Center the arrow
    arrow_padding = " " * (center_position - len(arrow) // 2)
    return (
        f"{formatted_top}\n"
        f"{arrow_padding}{arrow}\n"
        f"{formatted_bottom}"
    )


def sol_dollar_value():
    with open("data/sol_price", "r") as f:
        timestamp, price = map(float, f.read().split())
    # Fetch a new sol price if current is older than two minutes
    if time.time() - timestamp > 120:
        r = requests.get(
            "https://api.coinmarketcap.com/data-api/v3/cryptocurrency/market-pairs/latest?slug=solana&start=1&limit=10&category=spot&centerType=all&sort=cmc_rank_advanced&direction=desc&spotUntracked=true"
        )
        if r.status_code != 200:
            raise Exception("Failed to retreive a new SOL/USD price")
        prices = [p["price"] for p in r.json()["data"]["marketPairs"]]
        price = sum(prices) / len(prices)
        with open("data/sol_price", "w") as f:
            f.write(f"{time.time()} {price}")
    return price


def nice_float_price_format(price: float, underline=False) -> str:
    before_d, after_d = format(price, ".20f").split(".")
    zeros_after_d = len(after_d) - len(after_d.lstrip("0"))
    if price == 0:
        return "0"
    elif price >= 1_000_000_000_000:
        price = f"{price / 1_000_000_000_000:.2f}"
        return price.rstrip('0').rstrip('.')+'T'
    elif price >= 1_000_000_000:
        price = f"{price / 1_000_000_000:.2f}"
        return price.rstrip('0').rstrip('.')+'B'
    elif price >= 1_000_000:
        price = f"{price / 1_000_000:.2f}"
        return price.rstrip('0').rstrip('.')+'M'
    elif price >= 1_000:
        price = f"{price / 1_000:.2f}"
        return price.rstrip('0').rstrip('.')+'K'
    elif before_d != "0":
        return f"{price:.2f}".rstrip('0').rstrip('.')
    elif zeros_after_d > 2:
        return (
            f"0.0(<u>{zeros_after_d}</u>){after_d.lstrip('0')[:3].rstrip('0')}"
            if underline
            else f"0.0({zeros_after_d}){after_d.lstrip('0')[:3].rstrip('0')}"
        )
    else:
        return f"{price:.4f}".rstrip('0')


def get_blockhash():
    try:
        with open("data/blockhash", "r") as f:
            timestamp, blockhash_str = f.read().split()
            timestamp = float(timestamp)
    except FileNotFoundError:
        timestamp = 0
        blockhash_str = ""
    if time.time() - timestamp > 40:
        client = Client("https://api.mainnet-beta.solana.com")
        new_blockhash = client.get_latest_blockhash().value.blockhash
        with open("data/blockhash", "w") as f:
            f.write(f"{time.time()} {new_blockhash.__str__()}")
        return new_blockhash
    return Hash.from_string(blockhash_str)
