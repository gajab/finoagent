import asyncio
from sqlalchemy import text
from app.database import engine

async def migrate():
    print("Starting migration on database configured in app.config.settings.DATABASE_URL...")
    async with engine.begin() as conn:
        try:
            await conn.execute(text("ALTER TABLE users ADD COLUMN whatsapp_number VARCHAR(50);"))
            print("- Added whatsapp_number column.")
        except Exception as e:
            if "duplicate column name" in str(e).lower():
                print("- whatsapp_number column already exists.")
            else:
                print(f"- Error adding whatsapp_number: {e}")
                
        try:
            await conn.execute(text("ALTER TABLE users ADD COLUMN whatsapp_thread_id VARCHAR(100);"))
            print("- Added whatsapp_thread_id column.")
        except Exception as e:
            if "duplicate column name" in str(e).lower():
                print("- whatsapp_thread_id column already exists.")
            else:
                print(f"- Error adding whatsapp_thread_id: {e}")
                
        try:
            await conn.execute(text("CREATE INDEX ix_users_whatsapp_number ON users (whatsapp_number);"))
            print("- Added index on whatsapp_number.")
        except Exception as e:
            if "index ix_users_whatsapp_number already exists" in str(e).lower():
                print("- Index on whatsapp_number already exists.")
            else:
                print(f"- Error adding index: {e}")

if __name__ == "__main__":
    asyncio.run(migrate())
    print("Migration complete!")
