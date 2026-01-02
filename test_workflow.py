import asyncio
import logging
from src.workflows.examples.connecting_expertise import ConnectingExpertiseWorkflow
from src.db.connection import init_db

# Enable logging to see what's happening
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

async def main():
    # Initialize database tables (creates 'jobs' table if not exists)
    await init_db()

    workflow = ConnectingExpertiseWorkflow()

    input_data = {
        "username": "reda@rmasolutions.be",
        "password": "58s@N&w31lEh^g8q",
        "max_pages": 2,
        "output_dir": ".",  # CSV will be saved here
    }

    print("Starting Connecting Expertise workflow...")
    print(f"Max pages to scrape: {input_data['max_pages']}")
    print("-" * 50)

    try:
        result = await workflow.run(input_data, execution_id="test-001")

        print("\n" + "=" * 50)
        print("WORKFLOW COMPLETED")
        print("=" * 50)

        output = result.get("output_data", {})

        if output.get("error"):
            print(f"Error: {output['error']}")
        else:
            print(f"Jobs found: {output.get('count', 0)}")

            # Show CSV file location
            csv_file = output.get("csv_file")
            if csv_file:
                print(f"\nCSV file created: {csv_file}")

            print(f"\n{output.get('summary', 'No summary')}")

            # Show brief job list with description summaries
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
