# AWS step (planned, not done yet)

Nothing in this repo has been deployed to AWS yet. This is the plan for when I do it.

## Goal

Upload the Gold CSVs to a private S3 bucket and query them with Athena, using an IAM user or role that can only touch one folder in that bucket.

## Before creating anything

- [ ] Set up an AWS Budgets alert (note: it only sends an alert; it doesn't stop spending)
- [ ] Check current S3 and Athena pricing for the region I pick
- [ ] Never use root account keys
- [ ] Keep credentials out of the repo (`.gitignore` already blocks `.env`, `.aws/` and key files)

## Steps

1. Create a private S3 bucket with public access blocked and default encryption on.
2. Create an IAM policy that only allows `s3:PutObject` and `s3:GetObject` on `arn:aws:s3:::BUCKET/careops/*`, plus `s3:ListBucket` on the bucket, and attach it to the identity I'll use.
3. Dry run, which uploads nothing: `python run.py publish-s3 --bucket BUCKET`
4. Real upload: `pip install boto3`, then `python run.py publish-s3 --bucket BUCKET --confirm`
5. In Athena, set a query result location, then run `sql/athena_example.sql` with the bucket name filled in.
6. Save screenshots or outputs as evidence.
7. Clean up: delete the objects (including old versions if versioning is on), the Athena results and the bucket.

## Results

_Fill in after doing it: region, what was created, what worked, what it cost, when it was torn down._
