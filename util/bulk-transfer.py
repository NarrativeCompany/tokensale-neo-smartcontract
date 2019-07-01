import os
import json
from neo.contrib.narrative.bulk_process_tx import BulkProcess


class BulkTransfer(BulkProcess):

    from_address = None

    completed_jobs_path = None
    completed_jobs = None
    job_key = None

    def __init__(self):
        with open(os.path.join(os.path.abspath(os.path.dirname(__file__)), 'config', 'bulk-transfer-config.json'), 'r') as f:
            config = json.load(f)

        with open(os.path.join(os.path.abspath(os.path.dirname(__file__)), 'config', config['job_config_file']), 'r') as f:
            jobs = json.load(f)

        self.completed_jobs_path = os.path.join(os.path.abspath(os.path.dirname(__file__)), 'config', config['completed_job_file'])
        if os.path.exists(self.completed_jobs_path):
            with open(self.completed_jobs_path, 'r') as f:
                self.completed_jobs = json.load(f)
            # remove all completed jobs
            for job_key in self.completed_jobs:
                jobs.pop(job_key)
        else:
            self.completed_jobs = {}

        # don't want to use self.from_addr from BulkProcess since that has other behavior that we don't want.
        # it shouldn't be necessary anyway since there should only be a single address in the wallets being used.
        self.from_address = config['from_address']

        job_config = {
            "operation": "transfer",
            "operation_args_array_length": 3,
            "expected_result_count": 1,
            "jobs": jobs
        }

        super().__init__(config, job_config)

    def process_job(self):
        jobs_remaining = len(self.jobs)

        self.logger.debug("%s jobs processed. %s jobs remaining.", self.jobs_processed, jobs_remaining)

        # write this job to the completed jobs file with the transaction hash
        if self.job_key is not None and not self.test_only:
            self.completed_jobs[self.job_key] = self.tx_processing
            self.serialize_completed_jobs_file()

        self.job_key = None
        self.tx_processing = None

        if jobs_remaining > 0:
            # just pop a job out of the dict to process next
            self.job_key = list(self.jobs.keys())[0]
            job = self.jobs.pop(self.job_key)

            # construct the args for the transfer job testinvoke
            self.job = "['" + self.from_address + "','" + job['to_address'] + "'," + str(job['amount']) + "]"
        else:
            # change the jobs array to None (from an empty array) to indicate we are done and can shut down
            self.jobs = None

    def serialize_completed_jobs_file(self):
        with open(self.completed_jobs_path, 'w') as f:
            json.dump(self.completed_jobs, f)


def main():
    bulk_transfer = BulkTransfer()
    bulk_transfer.run()


if __name__ == "__main__":
    main()
