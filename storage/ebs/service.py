import boto3
import logging
import json
import os
import sys
from pathlib import Path
from datetime import datetime

# 수정: sys.path 조작 제거 및 설정 변수 이름 변경
# try:
#     current_dir = Path(__file__).resolve().parent
#     root_dir = current_dir.parent.parent
#     sys.path.append(str(root_dir))
#
#     from config.config import AWS_REGIONS as REGIONS, EBS_S3_BUCKET_NAME as S3_BUCKET_NAME
#     from storage.ebs.analyzer.analyzer import EBSAnalyzer
# except ImportError as e:
#     logging.error(f"모듈 임포트 중 오류 발생: {e}. 경로 설정을 확인하세요.")
#     REGIONS = ['ap-northeast-2']
#     S3_BUCKET_NAME = os.environ.get('EBS_S3_BUCKET_NAME', 'default-bucket-name')
#     EBSAnalyzer = None

# 직접 import 시도
try:
    from config.config import AWS_REGIONS, EBS_S3_BUCKET_NAME
    from storage.ebs.analyzer.analyzer import EBSAnalyzer
except ImportError as e:
    logging.error(f"Failed to import required modules: {e}. Ensure config and analyzer are available.")
    AWS_REGIONS = []
    EBS_S3_BUCKET_NAME = None
    EBSAnalyzer = None

# Import Slack messenger for updates
try:
    from integrations.slack.slack_messenger import _update_slack_message
except ImportError:
    _update_slack_message = None
    logging.warning("Failed to import _update_slack_message for progress updates.")

# 로깅 설정
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
# 필요시 핸들러 추가 (예: StreamHandler)
# handler = logging.StreamHandler()
# formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
# handler.setFormatter(formatter)
# logger.addHandler(handler)


def analyze_specific_volume(volume_id, region=None, detailed_report=False):
    """
    특정 볼륨 ID에 대한 분석을 수행합니다.
    (수정: 결과에 'attachments' 정보 포함)

    :param volume_id: 분석할 볼륨 ID
    :param region: 볼륨이 위치한 리전 (없으면 첫 번째 리전 사용)
    :param detailed_report: 상세 보고서 여부
    :return: 분석 결과
    """
    if EBSAnalyzer is None:
        logger.error("EBSAnalyzer가 제대로 임포트되지 않았습니다.")
        return {"error": "EBSAnalyzer 초기화 실패"}

    # 리전이 지정되지 않은 경우 첫 번째 리전 사용
    target_region = region if region else (AWS_REGIONS[0] if AWS_REGIONS else 'us-east-1')

    logger.info(f"특정 볼륨 분석 시작 - 볼륨 ID: {volume_id}, 리전: {target_region}")

    # EBS 분석기 초기화
    try:
        analyzer = EBSAnalyzer(target_region)
    except Exception as e:
        logger.error(f"EBSAnalyzer({target_region}) 초기화 중 오류: {e}", exc_info=True)
        return {
            "timestamp": datetime.now().isoformat(),
            "volume_id": volume_id,
            "region": target_region,
            "error": f"분석기 초기화 실패: {e}",
            "status": "오류 발생",
            "recommendation": "분석기 초기화 중 오류가 발생했습니다."
        }

    try:
        # 특정 볼륨 분석
        volume_result = analyzer.analyze_specific_volume(volume_id)

        # 오류 확인
        if 'error' in volume_result:
            logger.error(f"볼륨 {volume_id} 분석 중 오류: {volume_result['error']}")
            return {
                "timestamp": datetime.now().isoformat(),
                "volume_id": volume_id,
                "region": target_region,
                "error": volume_result['error'],
                "status": "오류 발생",
                "recommendation": "볼륨 정보를 가져올 수 없습니다. 볼륨 ID가 올바른지, 해당 리전에 존재하는지 확인하세요."
            }

        # 분석 결과 포맷팅 (attachments 추가)
        formatted_result = {
            "timestamp": datetime.now().isoformat(),
            "volume_id": volume_id,
            "region": target_region,
            "is_idle": volume_result.get('is_idle', False),
            "is_overprovisioned": volume_result.get('is_overprovisioned', False),
            "recommendation": volume_result.get('recommendation', '해당 없음'),
            "status": volume_result.get('status', '알 수 없음'),
            # analyze_specific_volume 결과에 Attachments 포함 안됨. format_volume_info 결과를 사용하도록 수정 필요
            # 또는 describe_volumes를 직접 호출해서 가져와야 함
            "attachments": volume_result.get('attached_instances', []), # format_volume_info 결과 키 사용
            "details": volume_result if detailed_report else {}
        }

        # 디버깅을 위한 분석 상세 정보
        if 'idle_check_details' in volume_result:
            formatted_result['idle_diagnosis'] = volume_result['idle_check_details']

        if 'overprovisioned_check_details' in volume_result:
            formatted_result['overprovisioned_diagnosis'] = volume_result['overprovisioned_check_details']

        # 권장 조치에 따라 작업 정의 (예시, 실제 로직은 더 복잡할 수 있음)
        if volume_result.get('is_idle', False):
            formatted_result["suggested_action"] = "idle_volume_action"
            formatted_result["action_params"] = {
                "volume_id": volume_id,
                "region": target_region,
                "action_type": "snapshot_and_delete" if "스냅샷 생성 후 볼륨 삭제" in volume_result.get('recommendation', '') else ("snapshot_only" if "스냅샷만" in volume_result.get('recommendation', '') else "change_type")
            }
        elif volume_result.get('is_overprovisioned', False):
            formatted_result["suggested_action"] = "overprovisioned_volume_action"
            # 필요한 action_type 추정 (예: resize 또는 change_type)
            action_type = "resize" # 기본값
            if "타입 변경" in volume_result.get('recommendation', ''):
                action_type = "change_type"
            if "크기" in volume_result.get('recommendation', '') and "타입" in volume_result.get('recommendation', ''):
                action_type = "change_type_and_resize"
                
            formatted_result["action_params"] = {
                "volume_id": volume_id,
                "region": target_region,
                "action_type": action_type
            }
        else:
            formatted_result["suggested_action"] = "none"
            formatted_result["action_params"] = {}

        return formatted_result
    except Exception as e:
        logger.error(f"볼륨 {volume_id} 분석 중 예외 발생: {str(e)}", exc_info=True)
        return {
            "timestamp": datetime.now().isoformat(),
            "volume_id": volume_id,
            "region": target_region,
            "error": str(e),
            "status": "예외 발생",
            "recommendation": "볼륨 분석 중 예기치 않은 오류가 발생했습니다."
        }

# analyze_all_regions 함수 이름 변경 및 로직 수정 -> analyze_all_ebs
def analyze_all_ebs(target_region=None, detailed_report=False, initial_message_ts=None, channel_id=None, bot_token=None):
    """
    지정된 리전 또는 모든 리전의 EBS 볼륨을 분석합니다.

    :param target_region: 분석할 특정 리전 (None이면 모든 리전)
    :param detailed_report: 상세 보고서 여부
    :param initial_message_ts: 업데이트할 초기 Slack 메시지 타임스탬프
    :param channel_id: Slack 채널 ID
    :param bot_token: Slack 봇 토큰
    :return: 분석 결과
    """
    if EBSAnalyzer is None:
        logger.error("EBSAnalyzer가 제대로 임포트되지 않았습니다.")
        return {"error": "EBSAnalyzer 초기화 실패"}

    regions_to_analyze = [target_region] if target_region else AWS_REGIONS
    if not regions_to_analyze:
        logger.warning("분석할 AWS 리전이 설정되지 않았습니다.")
        return {"error": "분석할 리전 없음"}

    all_results = {
        "timestamp": datetime.now().isoformat(),
        "regions": {},
        "summary": {
            "total_idle_volumes": 0,
            "total_overprovisioned_volumes": 0,
            "total_estimated_savings": 0,
            "total_volumes_analyzed": 0,
            "errors": [] # 오류 정보 추가
        },
        "idle_volumes": [], # 최상위 레벨로 이동
        "overprovisioned_volumes": [] # 최상위 레벨로 이동
    }
    total_regions = len(regions_to_analyze)
    processed_regions_count = 0

    for region in regions_to_analyze:
        logger.info(f"{region} 리전에 대한 EBS 볼륨 분석 시작")
        region_results = {
            "total_volumes_found": 0,
            "idle_volumes_count": 0,
            "overprovisioned_volumes_count": 0,
            "analyzed_volumes_count": 0,
            "estimated_savings": 0,
            "errors_count": 0,
            "details": [] # 상세 보고서용
        }
        all_results["regions"][region] = region_results

        try:
            analyzer = EBSAnalyzer(region)
            volumes_in_region = analyzer.get_all_ebs_volumes()
            region_volumes_total = len(volumes_in_region)
            region_results["total_volumes_found"] = region_volumes_total
            all_results["summary"]["total_volumes_analyzed"] += region_volumes_total
            logger.info(f"{region} 리전에서 {region_volumes_total}개의 볼륨 발견.")

            for i, volume_data in enumerate(volumes_in_region):
                 volume_id = volume_data.get('VolumeId')
                 if not volume_id:
                     logger.warning(f"{region} 리전에서 VolumeId 없는 볼륨 데이터 발견: {volume_data}")
                     region_results["errors_count"] += 1
                     all_results["summary"]["errors"].append({"region": region, "error": "VolumeId missing", "data": volume_data})
                     continue

                 # --- Progress Update Logic ---
                 if initial_message_ts and channel_id and bot_token and _update_slack_message and (i % 50 == 0 or i == region_volumes_total - 1):
                     progress_percent = ((processed_regions_count / total_regions) + ( (i + 1) / region_volumes_total if region_volumes_total > 0 else 0) / total_regions ) * 100
                     progress_text = f"Analyzing... Region {processed_regions_count + 1}/{total_regions} ({region}): Volume {i + 1}/{region_volumes_total}. Overall progress: {progress_percent:.1f}%"
                     logger.info(f"Sending progress update: {progress_text}")
                     progress_blocks = [{
                         "type": "context",
                         "elements": [{"type": "mrkdwn", "text": progress_text}]
                     }]
                     _update_slack_message(channel_id, bot_token, initial_message_ts, progress_blocks, progress_text)
                 # --- End Progress Update --- 

                 # 개별 볼륨 분석 (analyze_specific_volume 사용하지 않고 analyzer 내부 로직 재활용)
                 try:
                     # analyzer.format_volume_info 로 기본 정보 얻기
                     volume_info = analyzer.format_volume_info(volume_data)
                     
                     # 유휴 상태 분석
                     is_idle, idle_reason, idle_metrics = analyzer.idle_detector.is_idle_volume(volume_id, volume_info.get('metrics', {}))
                     volume_info['is_idle'] = is_idle
                     
                     # 과대 프로비저닝 분석
                     overprovisioned_result = analyzer.overprovisioned_detector.is_overprovisioned_volume(volume_id, volume_data)
                     volume_info['is_overprovisioned'] = overprovisioned_result is not None
                     
                     region_results["analyzed_volumes_count"] += 1
                     estimated_savings = 0
                     
                     # 결과 집계
                     if is_idle:
                         region_results["idle_volumes_count"] += 1
                         all_results["summary"]["total_idle_volumes"] += 1
                         idle_details = analyzer.idle_detector.detect_idle_volumes([volume_data])[0] # 상세 정보 다시 가져오기
                         volume_info['recommendation'] = idle_details.get('recommendation', 'Idle volume detected.')
                         monthly_cost = idle_details.get('monthly_cost', 0)
                         estimated_savings += monthly_cost
                         volume_info['estimated_savings'] = monthly_cost # 절감액 추가
                         all_results["idle_volumes"].append(volume_info) # 최상위 리스트에 추가
                         if detailed_report:
                             region_results["details"].append(volume_info)
                     elif overprovisioned_result:
                         region_results["overprovisioned_volumes_count"] += 1
                         all_results["summary"]["total_overprovisioned_volumes"] += 1
                         volume_info['recommendation'] = overprovisioned_result.get('recommendation_summary', 'Overprovisioned volume detected.')
                         savings_from_over = overprovisioned_result.get('details', {}).get('estimated_savings', 0)
                         estimated_savings += savings_from_over
                         volume_info['estimated_savings'] = savings_from_over # 절감액 추가
                         all_results["overprovisioned_volumes"].append(volume_info) # 최상위 리스트에 추가
                         if detailed_report:
                             region_results["details"].append(volume_info)
                     else:
                         # 최적화된 볼륨 (상세 보고서에만 포함)
                         if detailed_report:
                             volume_info['status'] = "Optimized/In-use"
                             volume_info['recommendation'] = "No optimization needed."
                             region_results["details"].append(volume_info)
                     
                     # 절감액 누적
                     region_results["estimated_savings"] += estimated_savings
                     all_results["summary"]["total_estimated_savings"] += estimated_savings
                     
                 except Exception as vol_ex:
                     logger.error(f"볼륨 {volume_id} 분석 중 오류: {str(vol_ex)}", exc_info=True)
                     region_results["errors_count"] += 1
                     all_results["summary"]["errors"].append({"region": region, "volume_id": volume_id, "error": str(vol_ex)})
                     if detailed_report:
                         region_results["details"].append({"volume_id": volume_id, "error": str(vol_ex)})

        except Exception as region_ex:
            logger.error(f"{region} 리전 분석 중 오류 발생: {str(region_ex)}", exc_info=True)
            all_results["summary"]["errors"].append({"region": region, "error": f"Region analysis failed: {str(region_ex)}"})
            # 리전 오류 발생 시 해당 리전 결과 초기화 또는 오류 표시
            all_results["regions"][region] = {"error": str(region_ex), "status": "Region analysis failed"}
        
        processed_regions_count += 1

    # 최종 결과 요약
    logger.info(f"모든 리전 분석 완료. 유휴 볼륨: {all_results['summary']['total_idle_volumes']}, 과대 프로비저닝 볼륨: {all_results['summary']['total_overprovisioned_volumes']}, 총 예상 절감액: ${all_results['summary']['total_estimated_savings']:.2f}")
    
    # 상세 보고서가 아니면 details 제거
    if not detailed_report:
        for reg in all_results["regions"]:
            if "details" in all_results["regions"][reg]:
                del all_results["regions"][reg]["details"]

    # S3 저장 로직은 여기서 제거 (필요시 다른 함수에서 호출)
    
    return all_results

def save_result_to_s3(result, prefix="ebs-analysis-results-"):
    """
    분석 결과를 S3 버킷에 JSON 파일로 저장

    :param result: 저장할 분석 결과 (딕셔너리)
    :param prefix: S3 객체 키 접두사
    :return: S3 저장 성공 여부 (boolean)
    """
    if not EBS_S3_BUCKET_NAME:
        logger.warning("S3 버킷 이름이 설정되지 않아 결과를 저장할 수 없습니다.")
        return False

    try:
        s3_client = boto3.client('s3')
        # 파일 이름 생성 (타임스탬프 사용)
        timestamp_str = result.get("timestamp", datetime.now().strftime("%Y%m%d-%H%M%S"))
        filename = f"{prefix}{timestamp_str}.json"

        # JSON 데이터 직렬화
        json_data = json.dumps(result, indent=4, default=str) # datetime 객체 등 직렬화 처리

        # S3에 업로드
        s3_client.put_object(
            Bucket=EBS_S3_BUCKET_NAME,
            Key=filename,
            Body=json_data,
            ContentType='application/json'
        )
        logger.info(f"분석 결과가 s3://{EBS_S3_BUCKET_NAME}/{filename} 에 성공적으로 저장되었습니다.")
        return True
    except Exception as e:
        logger.error(f"S3에 결과 저장 중 오류 발생: {str(e)}", exc_info=True)
        return False 