"""Optional: copy the Gold CSVs to an S3 bucket.

Nothing goes to AWS unless you pass --confirm. Without it, this only prints
where the files would go. Not tested against a real AWS account yet.
"""
from __future__ import annotations

from pathlib import Path

from .exports import export

# local file name -> folder inside the S3 prefix
S3_FOLDERS = {
    "gold_dim_facility.csv": "gold/dim_facility",
    "gold_dim_channel.csv": "gold/dim_channel",
    "gold_dim_date.csv": "gold/dim_date",
    "gold_fact_message.csv": "gold/fact_message",
    "gold_fact_shift.csv": "gold/fact_shift",
    "gold_daily_facility.csv": "gold/daily_facility",
    "manifest.json": "metadata",
}


def publish(root: Path, bucket: str, prefix: str = "careops", confirm: bool = False) -> list[str]:
    if not bucket or "/" in bucket or " " in bucket:
        raise ValueError("Provide a valid S3 bucket name")
    prefix = prefix.strip("/")
    if not prefix or ".." in prefix:
        raise ValueError("Provide a valid prefix")

    # Always rebuild and check the exports first.
    export(root)

    targets = {name: f"{prefix}/{folder}/{name}" for name, folder in S3_FOLDERS.items()}
    paths = [f"s3://{bucket}/{key}" for key in targets.values()]
    if not confirm:
        return paths

    try:
        import boto3
    except ImportError as error:
        raise RuntimeError("Install boto3 first: pip install boto3") from error

    client = boto3.client("s3")
    for name, key in targets.items():
        content_type = "application/json" if name.endswith(".json") else "text/csv"
        client.upload_file(
            str(root / "data" / "out" / name),
            bucket,
            key,
            ExtraArgs={"ServerSideEncryption": "AES256", "ContentType": content_type},
        )
    return paths
