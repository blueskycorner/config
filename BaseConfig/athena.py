import datetime
import re
import boto3
import os

TABLE_NAME = os.getenv('TABLE_NAME')
DATABASE_NAME = os.getenv('DATABASE_NAME')
ACCOUNT_ID = None # Determined at runtime
LATEST_PARTITION_VALUE = 'latest'

athena = boto3.client('athena')

def partitioner(event, context):
    global ACCOUNT_ID

    object_key = event['Records'][0]['s3']['object']['key']
    match = get_configuration_snapshot_object_key_match(object_key)
    if match is None:
        print('Ignoring event for non-configuration snapshot object key', object_key)
        return
    print('Adding partitions for configuration snapshot object key', object_key)
    
    ACCOUNT_ID = context.invoked_function_arn.split(':')[4]
    object_key_parent = 's3://{bucket_name}/{object_key_parent}/'.format(
        bucket_name=event['Records'][0]['s3']['bucket']['name'],
        object_key_parent=os.path.dirname(object_key))
    configuration_snapshot_region = get_configuration_snapshot_region(match)
    configuration_snapshot_date = get_configuration_snapshot_date(match)
    
    drop_partition(configuration_snapshot_region, LATEST_PARTITION_VALUE)
    add_partition(configuration_snapshot_region, LATEST_PARTITION_VALUE, object_key_parent)
    add_partition(configuration_snapshot_region, get_configuration_snapshot_date(match).strftime('%Y-%m-%d'), object_key_parent)
    
def get_configuration_snapshot_object_key_match(object_key):
    # Matches object keys like AWSLogs/123456789012/Config/us-east-1/2018/4/11/ConfigSnapshot/123456789012_Config_us-east-1_ConfigSnapshot_20180411T054711Z_a970aeff-cb3d-4c4e-806b-88fa14702hdb.json.gz
    return re.match('^AWSLogs/\d+/Config/([\w-]+)/(\d+)/(\d+)/(\d+)/ConfigSnapshot/[^\\\]+$', object_key)

def get_configuration_snapshot_region(match):
    return match.group(1)

def get_configuration_snapshot_date(match):
    return datetime.date(int(match.group(2)), int(match.group(3)), int(match.group(4)))
    
def add_partition(region_partition_value, dt_partition_value, partition_location):
    execute_query('ALTER TABLE {table_name} ADD PARTITION {partition} location \'{partition_location}\''.format(
        table_name=TABLE_NAME,
        partition=build_partition_string(region_partition_value, dt_partition_value),
        partition_location=partition_location))
        
def drop_partition(region_partition_value, dt_partition_value):
    execute_query('ALTER TABLE {table_name} DROP PARTITION {partition}'.format(
        table_name=TABLE_NAME,
        partition=build_partition_string(region_partition_value, dt_partition_value)))
        
def build_partition_string(region_partition_value, dt_partition_value):
    return "(dt='{dt_partition_value}', region='{region_partition_value}')".format(
        dt_partition_value=dt_partition_value,
        region_partition_value=region_partition_value)

def execute_query(query):
    print('Executing query:', query)
    query_output_location = 's3://aws-athena-query-results-{account_id}-{region}'.format(
        account_id=ACCOUNT_ID,
        region=os.environ['AWS_REGION'])
    start_query_response = athena.start_query_execution(
        QueryString=query,
        QueryExecutionContext={
            'Database': DATABASE_NAME
        },
        ResultConfiguration={
            'OutputLocation': query_output_location,
        }
    )
    print('Query started')
    
    is_query_running = True
    while is_query_running:
        get_query_execution_response = athena.get_query_execution(
            QueryExecutionId=start_query_response['QueryExecutionId']
        )
        query_state = get_query_execution_response['QueryExecution']['Status']['State']
        is_query_running = query_state == 'RUNNING'
        
        if not is_query_running and query_state != 'SUCCEEDED':
            raise Exception('Query failed')
    print('Query completed')