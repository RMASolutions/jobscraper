import asyncio
import logging
from src.workflows.examples.elia_jobs import EliaJobsWorkflow
from src.db.connection import init_db

# Enable logging to see what's happening
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

async def main():
    # Initialize database tables (creates 'jobs' table if not exists)
    await init_db()

    workflow = EliaJobsWorkflow()

    input_data = {
        "days_back": 7,        # Look back 7 days for emails (adjust as needed)
        "output_dir": ".",     # CSV will be saved here
    }

    print("Starting Elia/TAPFIN Jobs workflow...")
    print(f"Looking back {input_data['days_back']} days for job emails")
    print("-" * 50)

    try:
        result = await workflow.run(input_data, execution_id="elia-001")

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

            # Show job details
            jobs = output.get("jobs", [])
            if jobs:
                print("--- Job Details ---")
                for i, job in enumerate(jobs, 1):
                    print(f"\n{i}. {job['title']} ({job['reference']})")
                    print(f"   Department: {job.get('department', 'N/A')}")
                    print(f"   Level: {job.get('salary_band', 'N/A')}")
                    print(f"   Period: {job.get('start_date', 'N/A')} - {job.get('end_date', 'N/A')}")
                    print(f"   Deadline: {job.get('deadline', 'N/A')}")
                    print(f"   Link: {job.get('link', 'N/A')}")

        print("\nMessages:", result.get("messages", []))

    except Exception as e:
        print(f"\nWorkflow failed with error: {e}")
        raise

if __name__ == "__main__":
    asyncio.run(main())
