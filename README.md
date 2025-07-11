# AWS KMS Key Management Lambda

This AWS Lambda function automates the management of AWS KMS customer-managed keys (CMKs). It supports disabling, enabling, tagging, scheduling deletions, canceling deletions, and replicating keys to other regions.

## Features

- Disable keys and tag them with a `DisabledOn` timestamp
- Enable keys and remove the `DisabledOn` tag
- Schedule key deletion (only for keys that are disabled)
- Cancel deletion of keys in `PendingDeletion` or `PendingReplicaDeletion`
- Replicate a key to another region (e.g., `ca-central-1` to `eu-west-1`)
- Tag or remove `MigrationStatus=completed` for tracking migrations
- Hardcoded safety check to block execution in protected AWS account(s)

## How It Works

This Lambda is triggered manually or through automation (e.g., EventBridge, Step Functions, etc.). You pass an action and a list of KMS key ARNs, and the Lambda performs that action on each key.

## Deployment

1. Package the Python script into a ZIP file
2. Upload it to a Lambda function
3. Set the runtime to Python 3.9 or higher
4. Assign an IAM role with appropriate KMS permissions (see below)
5. Set a timeout appropriate for your use case (usually 1–2 minutes)
6. Use the test event format below to trigger actions

## Test Event Format

Here is an example JSON event you can use in the Lambda console:

```json
{
  "aws_region": "us-east-1",
  "action": "disable",
  "key_arns": [
    "arn:aws:kms:us-east-1:123456789012:key/key-id-1",
    "arn:aws:kms:us-east-1:123456789012:key/key-id-2"
  ],
  "dry_run": true,
  "deletion_schedule_days": 30
}
```

## Supported Actions

| Action                      | Description                                                   |
|-----------------------------|---------------------------------------------------------------|
| `disable`                   | Disable keys and tag them with `DisabledOn` date              |
| `enable`                    | Enable keys and remove `DisabledOn` tag if present            |
| `schedule_deletion`         | Schedule keys for deletion if they are disabled               |
| `cancel_deletion`           | Cancel deletion for keys in `PendingDeletion` state           |
| `replicate_ireland`         | Replicate key from `ca-central-1` to `eu-west-1` with alias   |
| `tag_srk_migration`         | Add `MigrationStatus=completed` tag                           |
| `remove_tag_srk_migration`  | Remove `MigrationStatus` tag                                  |

## Safety Controls

- A hardcoded list of blocked account IDs prevents execution in critical environments (e.g., production)
- Certain services (`rds`, `s3`, `efs`, etc.) are excluded from deletion by default
- Dry-run mode lets you preview actions without making changes

## Required IAM Permissions

The Lambda execution role must have KMS permissions such as:

```json
{
  "Effect": "Allow",
  "Action": [
    "kms:DescribeKey",
    "kms:DisableKey",
    "kms:EnableKey",
    "kms:ScheduleKeyDeletion",
    "kms:CancelKeyDeletion",
    "kms:ListResourceTags",
    "kms:TagResource",
    "kms:UntagResource",
    "kms:ListAliases",
    "kms:GetKeyPolicy",
    "kms:ReplicateKey",
    "kms:PutKeyPolicy",
    "kms:CreateAlias"
  ],
  "Resource": "*"
}
```

You can restrict this further based on your security policies.

## Notes

- Input `key_arns` must be full key ARNs, not aliases or key IDs
- Actions are idempotent; for example, enabling an already-enabled key is safe
- Aliases must follow the pattern `_ca-central-1` so the script can replace it when replicating to other regions

## Example CloudWatch Logs

```
Key arn:aws:kms:us-east-1:123:key-id disabled
Key arn:aws:kms:us-east-1:123:key-id scheduled for deletion in 30 days
Replica key created: alias/my-key_eu-west-1
```

## Authors

Created by Idrees K.

Feel free to modify for your organization’s specific use cases.
