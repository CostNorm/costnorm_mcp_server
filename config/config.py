"""
Configuration settings for costnorm_mcp_server.
"""

import os

# General AWS Settings
AWS_REGIONS = ['us-east-1', 'ap-northeast-2']  # List of AWS regions to analyze

# --- EBS Optimizer Settings --- 

# S3 bucket for storing analysis results
EBS_S3_BUCKET_NAME = os.environ.get('EBS_S3_BUCKET_NAME', 'ebs-optimizer-results-default')

# CloudWatch metric collection settings
EBS_METRIC_PERIOD = 86400  # Daily data (in seconds)

# Criteria for detecting idle EBS volumes
EBS_IDLE_VOLUME_CRITERIA = {
    'days_to_check': 7,                   # Detection period (days)
    'idle_time_threshold': 95,            # Idle time threshold (%)
    'io_ops_threshold': 10,               # Daily average IO operations threshold
    'throughput_threshold': 5 * 1024 * 1024,  # Daily average throughput threshold (5MB)
    'burst_balance_threshold': 90,        # Burst balance threshold (%)
    'detached_days_threshold': 7          # Threshold for days kept in detached state
}

# Criteria for detecting overprovisioned EBS volumes
EBS_OVERPROVISIONED_CRITERIA = {
    'days_to_check': 30,                 # Detection period (days) - Changed from months_to_check
    'disk_usage_threshold': 20,    # Disk usage threshold (%)
    'resize_buffer_percent': 0.3,        # Buffer percentage for resize recommendations (30%)
    'resize_min_buffer_gb': 10,          # Minimum buffer size in GB for resize
    'iops_usage_threshold_percent': 0.5, # IOPS usage threshold (50% of provisioned)
    'throughput_usage_threshold_percent': 0.5 # Throughput usage threshold (50% of provisioned)
    # 'min_size_gb': 100 # Removed, analyze all sizes by default
}

# Regional EBS pricing (USD/GB/month) - Consider using AWS Price List API for dynamic pricing
EBS_PRICING = {
    'us-east-1': {
        'gp2': 0.10,
        'gp3': 0.08,
        'io1': 0.125,
        'io2': 0.125,
        'st1': 0.045,
        'sc1': 0.025,
        'standard': 0.05
    },
    'ap-northeast-2': {
        'gp2': 0.114,
        'gp3': 0.0912,
        'io1': 0.138,
        'io2': 0.138,
        'st1': 0.051,
        'sc1': 0.028,
        'standard': 0.08
    },
    # Add other regions as needed
    'default': { # Default prices if region not found
        'gp2': 0.10,
        'gp3': 0.08,
        'io1': 0.125,
        'io2': 0.125,
        'st1': 0.045,
        'sc1': 0.025,
        'standard': 0.05
    }
}

# --- Other Settings --- 
# LOG_LEVEL = os.environ.get('LOG_LEVEL', 'INFO') 