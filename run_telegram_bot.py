from celeritas.db import UserDB
from celeritas.user import User
from celeritas.telegram_bot.bot import main

db = UserDB()

users = [
    User(
        id=7430379535, # nikdo nic
        wallet_public="EiKviBF8WYxqYEoS1QuyoNobs7qTr6GvYftUNzZhakeE",
        wallet_secret="4VghoutkLg5sMzBTtM2qYAEGcPvEksJh6dtokMYbE4RJMzHgVozVc1S8TaeW8w8QykJZzvgF8yAu7chX3HS17PYv",
        referrer=1,
        trading_fees_earned=1.5,
        trading_fees_paid_out=0.95,
        revenue=0.24
    ), 
#    User(
#        id=1564617170, # kxtof
#        wallet_public="EiKviBF8WYxqYEoS1QuyoNobs7qTr6GvYftUNzZhakeE",
#        wallet_secret="4VghoutkLg5sMzBTtM2qYAEGcPvEksJh6dtokMYbE4RJMzHgVozVc1S8TaeW8w8QykJZzvgF8yAu7chX3HS17PYv"
#    )
]
#[db.add_user(user, override=True) for user in users]

main()