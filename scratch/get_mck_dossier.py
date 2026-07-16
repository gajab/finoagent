import sys
import os
import asyncio

# Add backend to path so we can import app modules
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'backend')))

from app.services.debate_service import _build_evidence

async def main():
    print("Building dossier for MCK...")
    try:
        # Pass None for db so it fetches live data
        result = await _build_evidence(None, "MCK")
        print("\n================ COMPANY NAME ================")
        print(result["company_name"])
        print("\n================ DOSSIER CONTENT ================")
        # Print first 2000 chars and last 2000 chars to avoid overwhelming output, or full? 
        # Actually, let's save the full dossier to a text file, and print a summary.
        dossier = result["dossier"]
        with open("scratch/mck_dossier.txt", "w") as f:
            f.write(dossier)
        print(f"Full dossier saved to scratch/mck_dossier.txt ({len(dossier)} characters)")
        print("\nPreview of Dossier:")
        print(dossier[:2000])
        print("\n...\n")
        print(dossier[-2000:])
    except Exception as e:
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())
