import json

def detect_eips(ec2_client):
    unused_eips = []
    addresses = ec2_client.describe_addresses()['Addresses']
    for address in addresses:
            if 'InstanceId' not in address and 'NetworkInterfaceId' not in address:
                eip_id = address['AllocationId']
                unused_eips.append(eip_id)
    return unused_eips


def detect_enis(ec2_client):
    unused_enis = []
    enis = ec2_client.describe_network_interfaces(
            Filters=[{'Name': 'status', 'Values': ['available']}]
        )['NetworkInterfaces']
        
    for eni in enis:
        eni_id = eni['NetworkInterfaceId']
        unused_enis.append(eni_id)
    
    return unused_enis