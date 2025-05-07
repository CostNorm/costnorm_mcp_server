import boto3
import json
from detect_unattach import detect_eips, detect_enis

# Define the list of regions to check
# You can customize this list as needed.
TARGET_REGIONS = ['us-east-1', 'us-east-2', 'us-west-1', 'us-west-2', 'ap-south-1', 'ap-northeast-3', 'ap-northeast-2', 'ap-southeast-1', 'ap-southeast-2', 'ap-northeast-1', 'ca-central-1', 'eu-central-1', 'eu-west-1', 'eu-west-2', 'eu-west-3', 'eu-north-1', 'sa-east-1'] # Add more regions if needed

def lambda_handler(event, context):
    all_results = {}

    for region_name in TARGET_REGIONS:
        print(f"Processing region: {region_name}")
        try:
            regional_ec2_client = boto3.client('ec2', region_name=region_name)
            
            unused_eips = detect_eips(regional_ec2_client)
            unused_enis = detect_enis(regional_ec2_client)
            
            if unused_eips or unused_enis:
                all_results[region_name] = {
                    'unused_eips': unused_eips,
                    'unused_enis': unused_enis
                }
        except Exception as e:
            print(f"Error processing region {region_name}: {str(e)}")
            return {
                'statusCode': 500,
                'body': json.dumps({'error': str(e)})
            }

    return {
        'statusCode': 200,
        'body': json.dumps(all_results, indent=2)
    }

