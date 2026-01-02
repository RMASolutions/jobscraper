import asyncio
import logging
from src.workflows.examples.pro_unity import ProUnityWorkflow
from src.db.connection import init_db

# Enable logging to see what's happening
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

async def main():
    # Initialize database tables (creates 'jobs' table if not exists)
    await init_db()

    workflow = ProUnityWorkflow()

    input_data = {
        "username": "reda@rmasolutions.be",           # Replace with your Pro-Unity login
        "password": "ff$Y1S9cZJbX$vH10z^9",        # Replace with your password
        "max_pages": 2,
        "output_dir": ".",
    }

    print("Starting Pro-Unity workflow...")
    print(f"Max pages to scrape: {input_data['max_pages']}")
    print("-" * 50)

    try:
        result = await workflow.run(input_data, execution_id="prounity-001")

        print("\n" + "=" * 50)
        print("WORKFLOW COMPLETED")
        print("=" * 50)

        output = result.get("output_data", {})

        if output.get("error"):
            print(f"Error: {output['error']}")
        else:
            print(f"Jobs found: {output.get('count', 0)}")

            csv_file = output.get("csv_file")
            if csv_file:
                print(f"\nCSV file created: {csv_file}")

            print(f"\n{output.get('summary', 'No summary')}")

            jobs = output.get("jobs", [])
            if jobs:
                print("--- Job Summaries ---")
                for i, job in enumerate(jobs, 1):
                    print(f"\n{i}. {job['title']} ({job.get('client', 'N/A')})")
                    desc = job.get('description_summary', 'N/A')
                    print(f"   {desc[:200]}{'...' if len(desc) > 200 else ''}")

        print("\nMessages:", result.get("messages", []))

    except Exception as e:
        print(f"\nWorkflow failed with error: {e}")
        raise

if __name__ == "__main__":
    asyncio.run(main())
