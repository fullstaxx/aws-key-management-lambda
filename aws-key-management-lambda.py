import boto3
import logging
import datetime
from botocore.exceptions import ClientError

# Set up logging to CloudWatch. Info level is usually sufficient for these kinds of ops scripts.
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Helper to get the status of a KMS key (e.g., Enabled, Disabled, PendingDeletion, etc.)
def key_status(kms_client, arn):
    try:
        response = kms_client.describe_key(KeyId=arn)
        return response['KeyMetadata']['KeyState']
    except ClientError as e:
        if e.response['Error']['Code'] == 'NotFoundException':
            return 'NotFound'
        else:
            raise

# Disable a list of keys and tag them with the current date
def disable_keys(kms_client, key_arns, dry_run=False):
    for arn in key_arns:
        if dry_run:
            # Just simulate what would happen
            status = key_status(kms_client, arn)
            if status != 'NotFound':
                logger.info(f"Key {arn} would be disabled. (Dry Run)")
            else:
                logger.info(f"Key {arn} not found. (Dry Run)")
        else:
            try:
                kms_client.disable_key(KeyId=arn)
                kms_client.tag_resource(
                    KeyId=arn,
                    Tags=[{
                        'TagKey': 'DisabledOn',
                        'TagValue': datetime.datetime.now().strftime('%Y-%m-%d')
                    }]
                )
                logger.info(f"Key {arn} disabled")
            except ClientError as e:
                logger.error(f"Failed to disable key {arn}: {e}")

# Enable a list of keys and remove the "DisabledOn" tag if present
def enable_keys(kms_client, key_arns):
    for arn in key_arns:
        try:
            status = key_status(kms_client, arn)
            if status == 'NotFound':
                logger.info(f"Key {arn} not found")
                continue

            kms_client.enable_key(KeyId=arn)
            tags = kms_client.list_resource_tags(KeyId=arn)
            if any(tag['TagKey'] == 'DisabledOn' for tag in tags['Tags']):
                kms_client.untag_resource(KeyId=arn, TagKeys=['DisabledOn'])
            logger.info(f"Key {arn} enabled")
        except ClientError as e:
            logger.error(f"Failed to enable key {arn}: {e}")

# Schedule keys for deletion if they're not in use by certain AWS services
def schedule_key_deletion(kms_client, key_arns, deletion_days, dry_run=False):
    # Some services like RDS or S3 often require active keys, so we skip those
    excluded_services = ['dynamodb', 'efs', 'elasticache', 'rds', 's3']

    for arn in key_arns:
        try:
            status = key_status(kms_client, arn)
            if status == 'PendingDeletion':
                logger.info(f"Key {arn} already scheduled for deletion.")
                continue

            tags = kms_client.list_resource_tags(KeyId=arn)
            service_tag = next((tag for tag in tags['Tags'] if tag['TagKey'] == 'service_name'), None)

            if service_tag and service_tag['TagValue'] in excluded_services:
                logger.warning(f"Key {arn} is in use by {service_tag['TagValue']} — skipping deletion.")
                continue

            disabled_tag = next((tag for tag in tags['Tags'] if tag['TagKey'] == 'DisabledOn'), None)

            if disabled_tag:
                if dry_run:
                    logger.info(f"Key {arn} would be scheduled for deletion in {deletion_days} days. (Dry Run)")
                else:
                    kms_client.schedule_key_deletion(KeyId=arn, PendingWindowInDays=deletion_days)
                    logger.info(f"Key {arn} scheduled for deletion in {deletion_days} days.")
            else:
                logger.info(f"Key {arn} is not disabled — skipping deletion.")
        except ClientError as e:
            if e.response['Error']['Code'] == 'NotFoundException':
                logger.info(f"Key {arn} not found.")
            else:
                logger.error(f"Failed to schedule deletion for key {arn}: {e}")

# Cancel any keys currently marked for deletion
def cancel_key_deletion(kms_client, key_arns):
    for arn in key_arns:
        try:
            status = key_status(kms_client, arn)
            if status in ['PendingDeletion', 'PendingReplicaDeletion']:
                kms_client.cancel_key_deletion(KeyId=arn)
                logger.info(f"Cancelled deletion for key {arn}")
            else:
                logger.info(f"Key {arn} is not scheduled for deletion — nothing to cancel.")
        except ClientError as e:
            logger.error(f"Error cancelling deletion for key {arn}: {e}")

# Tag a key to indicate it has completed migration
def tag_srk_migration(kms_client, key_arns):
    for arn in key_arns:
        try:
            kms_client.tag_resource(
                KeyId=arn,
                Tags=[{'TagKey': 'MigrationStatus', 'TagValue': 'completed'}]
            )
            logger.info(f"Tagged key {arn} with MigrationStatus=completed")
        except ClientError as e:
            logger.error(f"Failed to tag key {arn}: {e}")

# Remove the migration tag from a key
def remove_tag_srk_migration(kms_client, key_arns):
    for arn in key_arns:
        try:
            kms_client.untag_resource(KeyId=arn, TagKeys=['MigrationStatus'])
            logger.info(f"Removed MigrationStatus tag from key {arn}")
        except ClientError as e:
            logger.error(f"Failed to remove tag from key {arn}: {e}")

# Helper to retrieve a key's alias based on its ARN
def get_primary_alias(kms_client, key_arn):
    try:
        aliases = kms_client.list_aliases(KeyId=key_arn)
        for alias in aliases['Aliases']:
            if alias['TargetKeyId'] == key_arn.split('/')[-1]:
                return alias['AliasName']
    except ClientError as e:
        logger.error(f"Error getting alias for key {key_arn}: {e}")
    return None

# Create a replica of the key in another region (like ca-central-1 → eu-west-1)
def replicate_key(session, primary_key_arn, primary_alias, secondary_region, dry_run=False):
    secondary_alias = primary_alias.replace('_ca-central-1', f'_{secondary_region}')
    kms_primary = session.client('kms', region_name='ca-central-1')
    kms_secondary = session.client('kms', region_name=secondary_region)

    try:
        policy = kms_primary.get_key_policy(KeyId=primary_key_arn, PolicyName='default')['Policy']
        tags = kms_primary.list_resource_tags(KeyId=primary_key_arn)['Tags']

        if dry_run:
            logger.info(f"Would replicate key {primary_key_arn} to {secondary_region} with alias {secondary_alias}. (Dry Run)")
            return

        response = kms_primary.replicate_key(
            KeyId=primary_key_arn,
            ReplicaRegion=secondary_region,
            Description='Replica key'
        )
        replica_arn = response['ReplicaKeyMetadata']['Arn']

        kms_secondary.put_key_policy(KeyId=replica_arn, PolicyName='default', Policy=policy)
        kms_secondary.create_alias(AliasName=secondary_alias, TargetKeyId=replica_arn)

        if tags:
            kms_secondary.tag_resource(KeyId=replica_arn, Tags=tags)

        logger.info(f"Replica key created with alias {secondary_alias}")
    except ClientError as e:
        logger.error(f"Error replicating key {primary_key_arn}: {e}")

# Entry point for the Lambda function
def lambda_handler(event, context):
    # Region to operate in; default to us-east-1 if not provided
    aws_region = event.get('aws_region', 'us-east-1')
    action = event.get('action')
    key_arns = event.get('key_arns', [])
    dry_run = event.get('dry_run', False)
    deletion_days = event.get('deletion_schedule_days', 30)

    # This is where you hardcode production accounts that should NEVER run this
    blocked_accounts = ["111122223333"]  # Replace with your actual prod account ID(s)

    # Get current AWS account ID
    sts = boto3.client('sts')
    account_id = sts.get_caller_identity().get('Account')

    # Safety check: block execution in protected accounts
    if account_id in blocked_accounts:
        logger.warning(f"Execution blocked in account {account_id}")
        return {
            'statusCode': 403,
            'body': 'Execution not allowed in this account'
        }

    session = boto3.Session()
    kms_client = session.client('kms', region_name=aws_region)

    # If no key ARNs were provided, fail early
    if not key_arns:
        logger.error("No key ARNs provided.")
        return {
            'statusCode': 400,
            'body': 'No key ARNs specified'
        }

    # Execute the requested action
    if action == 'disable':
        disable_keys(kms_client, key_arns, dry_run)
    elif action == 'enable':
        enable_keys(kms_client, key_arns)
    elif action == 'schedule_deletion':
        schedule_key_deletion(kms_client, key_arns, deletion_days, dry_run)
    elif action == 'cancel_deletion':
        cancel_key_deletion(kms_client, key_arns)
    elif action == 'tag_srk_migration':
        tag_srk_migration(kms_client, key_arns)
    elif action == 'remove_tag_srk_migration':
        remove_tag_srk_migration(kms_client, key_arns)
    elif action == 'replicate_ireland':
        secondary_region = 'eu-west-1'
        for arn in key_arns:
            alias = get_primary_alias(kms_client, arn)
            if alias:
                replicate_key(session, arn, alias, secondary_region, dry_run)
            else:
                logger.warning(f"No alias found for {arn}, skipping replication.")
    else:
        logger.error(f"Unsupported action: {action}")
        return {
            'statusCode': 400,
            'body': f"Unsupported action: {action}"
        }

    return {
        'statusCode': 200,
        'body': f"Action {action} completed"
    }
