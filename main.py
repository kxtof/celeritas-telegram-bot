import time
import asyncio
import json
from celeritas.db import UserDB, TokenDB
from celeritas.user import User

from celeritas.telegram_bot.bot import main
from celeritas.telegram_bot.utils import sol_dollar_value

from celeritas.constants import client, aclient, LAMPORTS_PER_SOL 
from solders.pubkey import Pubkey
from celeritas.transact import Transact
from solana.rpc.types import TxOpts

db = UserDB()
tokendb = TokenDB()

#print(asyncio.run(tokendb.update_price('4Az1L7zufT6vFx8EETMm5Jvu7et9aoYEEnRN84Rzpump')))
#exit()
#[print(db.get_user(i).to_dict()['trading_fees_earned']) for i in (1, 7430379535, 3, 2, 1564617170)]

# refresh DBs
#tokendb.tokens.drop()
#db.users.drop()

#db.delete_user(7430379535)

"""
transact = Transact(
    '4VghoutkLg5sMzBTtM2qYAEGcPvEksJh6dtokMYbE4RJMzHgVozVc1S8TaeW8w8QykJZzvgF8yAu7chX3HS17PYv'
)
quote = asyncio.run(transact.snipe_pump_fun(
    '9VpbSaYXmofpZrTenAGroifi8tQevqgt9XxApeVKpump',
    10_000,
    0.001,
    "4orGcbg92xJb6ABPyLrzwC9bJv4kGqFtrikg1q1weB4K",
    "49pB7DAFofbFLXkXx8mBZHGJjhCvQZFgmzUzzkA3H5ZM"
))
"""
users = [
    User(
        id=1, # kxtof
        wallet_public="EiKviBF8WYxqYEoS1QuyoNobs7qTr6GvYftUNzZhakeE",
        wallet_secret="4VghoutkLg5sMzBTtM2qYAEGcPvEksJh6dtokMYbE4RJMzHgVozVc1S8TaeW8w8QykJZzvgF8yAu7chX3HS17PYv",
    ), User(
        id=7430379535, # nikdo nic
        wallet_public="EiKviBF8WYxqYEoS1QuyoNobs7qTr6GvYftUNzZhakeE",
        wallet_secret="4VghoutkLg5sMzBTtM2qYAEGcPvEksJh6dtokMYbE4RJMzHgVozVc1S8TaeW8w8QykJZzvgF8yAu7chX3HS17PYv",
        referrer=1,
    ), User(
        id=3, # kxtof
        wallet_public="EiKviBF8WYxqYEoS1QuyoNobs7qTr6GvYftUNzZhakeE",
        wallet_secret="4VghoutkLg5sMzBTtM2qYAEGcPvEksJh6dtokMYbE4RJMzHgVozVc1S8TaeW8w8QykJZzvgF8yAu7chX3HS17PYv",
        referrer=7430379535,
    ), User(
        id=2, # kxtof
        wallet_public="EiKviBF8WYxqYEoS1QuyoNobs7qTr6GvYftUNzZhakeE",
        wallet_secret="4VghoutkLg5sMzBTtM2qYAEGcPvEksJh6dtokMYbE4RJMzHgVozVc1S8TaeW8w8QykJZzvgF8yAu7chX3HS17PYv",
        referrer=3,
    ), User(
        id=1564617170, # kxtof
        wallet_public="EiKviBF8WYxqYEoS1QuyoNobs7qTr6GvYftUNzZhakeE",
        wallet_secret="4VghoutkLg5sMzBTtM2qYAEGcPvEksJh6dtokMYbE4RJMzHgVozVc1S8TaeW8w8QykJZzvgF8yAu7chX3HS17PYv",
        referrer=2,
    )
]
#[db.add_user(user) for user in users]

#db.update_sol_balance(7430379535)

#db.update_user_holdings(7430379535)

#exit()

# kxtof user_id
# 1564617170

"""
db.update_attribute(
    7430379535,
    "transactions",
    [
        {'timestamp': int(time.time())-24*60*60, 'mint': 'C3JX9TWLqHKmcoTDTppaJebX2U7DcUQDEHVSmJFz6K6S', 'pre_sol_balance': 0.007924325, 'post_sol_balance': 0.003074325, 'pre_token_balance': 0, 'post_token_balance': 47.662539, 'sol_dollar_value': 141.79732557300002, 'fee_paid': 2e-05},
    ]
)

print(db.get_attribute(7430379535, "transactions"))
for key, position in db.update_user_positions(7430379535, get_prices=True).items():
    print(key)
    print('', position)
    print()
"""
#print(db.update_user_positions(7430379535))
db.update_attribute(1564617170, "transactions", [])
db.update_attribute(7430379535, "sniping", [])
#db.update_user_holdings(7430379535)


#user = db.get_user(7430379535)
#print(user.holdings)

from celeritas.pump_fun_sniper import main
asyncio.run(main())

main()