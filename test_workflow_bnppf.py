import asyncio
import logging
from src.workflows.examples.bnppf_jobs import BNPPFJobsWorkflow
from src.db.connection import init_db

# Enable logging to see what's happening
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

async def main():
    # Initialize database tables (creates 'jobs' table if not exists)
    await init_db()

    workflow = BNPPFJobsWorkflow()

    input_data = {
        "days_back": 7,        # Look back 7 days for emails (adjust as needed)
        "output_dir": ".",     # CSV will be saved here
    }

    print("Starting BNPP Fortis Jobs workflow...")
    print(f"Looking back {input_data['days_back']} days for job emails")
    print("-" * 50)

    try:
        result = await workflow.run(input_data, execution_id="bnppf-001")

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
                print("--- Job Summaries ---")
                for i, job in enumerate(jobs, 1):
                    print(f"\n{i}. {job['title']} ({job['reference']})")
                    print(f"   Location: {job.get('location', 'N/A')}")
                    print(f"   Period: {job.get('start_date', 'N/A')} - {job.get('end_date', 'N/A')}")
                    print(f"   Telework: {job.get('telework', 'N/A')}")
                    desc = job.get('description_summary', 'N/A')
                    print(f"   Summary: {desc[:200]}{'...' if len(str(desc)) > 200 else ''}")

        print("\nMessages:", result.get("messages", []))

    except Exception as e:
        print(f"\nWorkflow failed with error: {e}")
        raise

if __name__ == "__main__":
    asyncio.run(main())
