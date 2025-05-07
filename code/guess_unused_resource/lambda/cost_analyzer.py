# cost_analyzer.py
import boto3
import csv
import io
import os
from datetime import datetime, timedelta, timezone

# 환경 변수 읽기 (기본값 설정 포함)
S3_BUCKET_NAME = os.environ.get('S3_BUCKET_NAME', 'day-by-day')
RECENT_PERIOD_DAYS = int(os.environ.get('RECENT_PERIOD_DAYS', 7))
COMPARISON_PERIOD_DAYS = int(os.environ.get('COMPARISON_PERIOD_DAYS', 30))
COST_THRESHOLD_PERCENTAGE = float(os.environ.get('COST_THRESHOLD_PERCENTAGE', 1.0))
# 새로운 환경 변수 추가 (기본값: $0.01)
MIN_COMPARISON_COST = float(os.environ.get('MIN_COMPARISON_COST', 0.01))
FILE_SUFFIX = os.environ.get('FILE_SUFFIX', '_sorted_costs.csv')

s3_client = boto3.client('s3', 'ap-northeast-2')

def calculate_date_ranges(recent_period_days, comparison_period_days):
    """비용 비교를 위한 최근 기간과 비교 기간의 시작/종료 날짜를 계산합니다."""
    today = datetime.now(timezone.utc).date()
    recent_end_date = today - timedelta(days=1)
    recent_start_date = recent_end_date - timedelta(days=recent_period_days - 1)
    comparison_end_date = recent_start_date - timedelta(days=1)
    comparison_start_date = comparison_end_date - timedelta(days=comparison_period_days - 1)
    print(f"Date ranges calculated: Recent ({recent_start_date} to {recent_end_date}), Comparison ({comparison_start_date} to {comparison_end_date})")
    return recent_start_date, recent_end_date, comparison_start_date, comparison_end_date

def get_costs_for_period(bucket, start_date, end_date):
    """지정된 기간 동안의 비용 데이터를 S3에서 읽어 집계합니다."""
    costs = {} # {"Service::Operation": total_cost}
    current_date = start_date
    print(f"Fetching cost data from {start_date.strftime('%y%m%d')} to {end_date.strftime('%y%m%d')}")

    while current_date <= end_date:
        file_name = f"{current_date.strftime('%y%m%d')}{FILE_SUFFIX}"
        try:
            response = s3_client.get_object(Bucket=bucket, Key=file_name)
            content = response['Body'].read().decode('utf-8')
            csv_reader = csv.reader(io.StringIO(content))
            header = next(csv_reader)
            try:
                service_col = next(i for i, col in enumerate(header) if col.lower() == 'service')
                operation_col = next(i for i, col in enumerate(header) if col.lower() == 'operation')
                cost_col = next(i for i, col in enumerate(header) if col.lower() == 'cost')
            except StopIteration:
                print(f"Warning: Required columns (Service, Operation, Cost) not found in {file_name}. Skipping file.")
                current_date += timedelta(days=1)
                continue

            for row in csv_reader:
                if len(row) > max(service_col, operation_col, cost_col):
                    try:
                        service = row[service_col]
                        operation = row[operation_col]
                        cost_str = row[cost_col]
                        cost = float(cost_str)
                        resource_key = f"{service}::{operation}"
                        costs[resource_key] = costs.get(resource_key, 0.0) + cost
                    except ValueError:
                        print(f"Warning: Could not parse cost '{cost_str}' in row: {row} from file {file_name}. Skipping row.")
                    except IndexError:
                         print(f"Warning: Row {row} in file {file_name} does not have enough columns. Skipping row.")

        except s3_client.exceptions.NoSuchKey:
            print(f"Info: File not found for date {current_date.strftime('%y%m%d')}: {file_name}")
        except Exception as e:
            print(f"Error reading file {file_name}: {e}")

        current_date += timedelta(days=1)

    print(f"Finished fetching cost data. Found {len(costs)} distinct Service::Operation pairs.")
    return costs

def identify_unused_resources(comparison_costs, recent_costs, cost_threshold_percentage):
    """비용 추세를 기반으로 잠재적으로 사용되지 않는 Service::Operation 쌍을 식별합니다."""
    unused_resources = []
    cost_threshold_ratio = cost_threshold_percentage / 100.0
    print("\nIdentifying potentially unused Service::Operation pairs based on cost trend...")
    print(f"  Criteria: Recent cost < {cost_threshold_percentage}% of comparison cost AND Comparison cost >= ${MIN_COMPARISON_COST:.4f}") # 기준 명시

    # 비교 기간 비용 데이터 기준으로 반복
    for resource, comp_cost in comparison_costs.items():
        # Criteria 1: Significant cost decrease (and comparison cost meets minimum)
        if comp_cost >= MIN_COMPARISON_COST:
            recent_cost = recent_costs.get(resource, 0.0)
            if recent_cost < (comp_cost * cost_threshold_ratio):
                unused_info = {
                    "resource": resource,
                    "comparison_period_cost": comp_cost,
                    "recent_period_cost": recent_cost
                    # "reason" 키 제거
                }
                unused_resources.append(unused_info)
                # 디버깅을 위한 상세 정보 출력 (단순화)
                print(f"  [Potential Unused] Resource: {resource}")
                print(f"    - Comparison Period Cost: ${comp_cost:.4f} (>= ${MIN_COMPARISON_COST:.4f})")
                print(f"    - Recent Period Cost    : ${recent_cost:.4f} (< {cost_threshold_percentage}% of comparison cost)")

    print(f"\nIdentified {len(unused_resources)} potentially unused Service::Operation pairs meeting the criteria.")
    return unused_resources

def find_potentially_unused_resources():
    global MIN_COMPARISON_COST 
    """S3 비용 데이터를 분석하여 잠재적으로 사용되지 않는 리소스 목록을 반환합니다."""
    # 환경 변수 유효성 검사
    if not S3_BUCKET_NAME or 'your-cost-data-bucket-name' in S3_BUCKET_NAME:
        raise ValueError("Error: S3_BUCKET_NAME environment variable is not set correctly.")
    # MIN_COMPARISON_COST 유효성 검사 (선택사항이지만 추가)
    if MIN_COMPARISON_COST < 0:
         print(f"Warning: MIN_COMPARISON_COST ({MIN_COMPARISON_COST}) is negative. Using 0.0 instead.")
         # 음수 값 방지 위해 0으로 설정하거나 에러 발생 시킬 수 있음
         # 전역 변수 수정 위해 필요
         MIN_COMPARISON_COST = 0.0


    # 날짜 범위 계산
    recent_start_date, recent_end_date, comparison_start_date, comparison_end_date = \
        calculate_date_ranges(RECENT_PERIOD_DAYS, COMPARISON_PERIOD_DAYS)

    # 비용 계산
    print("\nCalculating costs for comparison period...")
    comparison_costs = get_costs_for_period(S3_BUCKET_NAME, comparison_start_date, comparison_end_date)
    print("\nCalculating costs for recent period...")
    recent_costs = get_costs_for_period(S3_BUCKET_NAME, recent_start_date, recent_end_date)

    # 미사용 리소스 식별
    unused_resources = identify_unused_resources(
        comparison_costs, recent_costs, COST_THRESHOLD_PERCENTAGE
    )

    return unused_resources
