import boto3
import logging
from datetime import datetime, timedelta

from .idle_detector import IdleVolumeDetector
from .overprovisioned_detector import OverprovisionedVolumeDetector
from config.config import EBS_IDLE_VOLUME_CRITERIA as IDLE_VOLUME_CRITERIA, \
                        EBS_OVERPROVISIONED_CRITERIA as OVERPROVISIONED_CRITERIA, \
                        EBS_METRIC_PERIOD as METRIC_PERIOD
from storage.ebs.utils import calculate_monthly_cost, get_tags_as_dict

logger = logging.getLogger()

class EBSAnalyzer:
    """
    EBS 볼륨 분석기 - 유휴 상태와 과대 프로비저닝된 볼륨을 식별
    """

    def __init__(self, region):
        """
        :param region: 분석할 AWS 리전
        """
        self.region = region
        self.ec2_client = boto3.client('ec2', region_name=region)
        self.cloudwatch_client = boto3.client('cloudwatch', region_name=region)

        # 감지기 초기화
        self.idle_detector = IdleVolumeDetector(
            region,
            self.ec2_client,
            self.cloudwatch_client,
            IDLE_VOLUME_CRITERIA
        )

        self.overprovisioned_detector = OverprovisionedVolumeDetector(
            region,
            self.ec2_client,
            self.cloudwatch_client,
            OVERPROVISIONED_CRITERIA
        )

    def get_all_ebs_volumes(self):
        """
        모든 EBS 볼륨 정보를 수집

        :return: 볼륨 정보 리스트
        """
        volumes = []
        next_token = None

        while True:
            if next_token:
                response = self.ec2_client.describe_volumes(NextToken=next_token)
            else:
                response = self.ec2_client.describe_volumes()

            volumes.extend(response['Volumes'])

            if 'NextToken' in response:
                next_token = response['NextToken']
            else:
                break

        logger.info(f"{self.region} 리전에서 {len(volumes)}개 EBS 볼륨을 발견했습니다.")
        return volumes

    def get_volume_metrics(self, volume_id, volume_type):
        """
        볼륨의 CloudWatch 메트릭 데이터를 수집

        :param volume_id: EBS 볼륨 ID
        :param volume_type: 볼륨 유형
        :return: 수집된 메트릭 데이터
        """
        end_time = datetime.now()
        start_time = end_time - timedelta(days=IDLE_VOLUME_CRITERIA['days_to_check'])

        # 수집할 기본 메트릭 목록
        metric_names = [
            'VolumeIdleTime',
            'VolumeReadOps',
            'VolumeWriteOps',
            'VolumeReadBytes',
            'VolumeWriteBytes',
            'VolumeTotalReadTime',
            'VolumeTotalWriteTime',
            'VolumeQueueLength'
        ]

        # 볼륨 유형에 따라 BurstBalance 메트릭 추가
        if volume_type in ['gp2', 'st1', 'sc1']:
            metric_names.append('BurstBalance')

        # 메트릭 데이터 수집
        metrics_data = {}

        for metric_name in metric_names:
            try:
                response = self.cloudwatch_client.get_metric_statistics(
                    Namespace='AWS/EBS',
                    MetricName=metric_name,
                    Dimensions=[{'Name': 'VolumeId', 'Value': volume_id}],
                    StartTime=start_time,
                    EndTime=end_time,
                    Period=METRIC_PERIOD,
                    Statistics=['Average', 'Maximum', 'Minimum', 'Sum']
                )

                if response['Datapoints']:
                    # 가장 최근 데이터
                    latest = max(response['Datapoints'], key=lambda x: x['Timestamp'])

                    # 전체 기간 평균
                    avg_value = sum(dp['Average'] for dp in response['Datapoints']) / len(response['Datapoints']) if response['Datapoints'] else 0
                    max_value = max(dp['Maximum'] for dp in response['Datapoints']) if response['Datapoints'] else 0
                    min_value = min(dp['Minimum'] for dp in response['Datapoints']) if response['Datapoints'] else 0

                    # 메트릭 요약 정보 저장
                    metrics_data[metric_name] = {
                        'latest': latest['Average'],
                        'average': avg_value,
                        'maximum': max_value,
                        'minimum': min_value,
                        'unit': latest['Unit'],
                        'datapoints_count': len(response['Datapoints'])
                    }
            except Exception as e:
                logger.warning(f"{volume_id} 볼륨의 {metric_name} 메트릭 조회 중 오류 발생: {str(e)}")

        return metrics_data

    def simplify_metrics(self, metrics):
        """
        메트릭 데이터를 간략화 - avg 값만 표시

        :param metrics: 원본 메트릭 데이터
        :return: 간략화된 메트릭 데이터 (avg 값만 포함)
        """
        if not metrics:
            return {}

        simplified = {}

        # 핵심 메트릭만 포함 (분석에 필요한 것들)
        key_metrics = ['VolumeIdleTime', 'VolumeReadOps', 'VolumeWriteOps', 
                      'VolumeReadBytes', 'VolumeWriteBytes', 'BurstBalance']

        for metric_name in key_metrics:
            if metric_name in metrics:
                # avg 값만 포함
                simplified[metric_name] = metrics[metric_name].get('average', 0)

                # VolumeIdleTime은 퍼센트로 변환된 값도 추가
                if metric_name == 'VolumeIdleTime':
                    idle_seconds = metrics[metric_name].get('average', 0)
                    idle_percent = (idle_seconds / 60) * 100 if idle_seconds else 0
                    simplified[f"{metric_name}_percent"] = idle_percent

        # 다른 메트릭 추가는 필요한 경우 주석 해제
        # if 'VolumeQueueLength' in metrics:
        #     simplified['QueueLength'] = metrics['VolumeQueueLength'].get('average', 0)

        return simplified

    def format_volume_info(self, volume):
        """
        볼륨 정보를 일관된 형식으로 포맷팅

        :param volume: EC2 API에서 반환된 볼륨 정보
        :return: 포맷팅된 볼륨 정보 딕셔너리
        """
        volume_id = volume['VolumeId']
        volume_type = volume['VolumeType']

        # 기본 볼륨 정보
        volume_info = {
            'volume_id': volume_id,
            'volume_type': volume_type,
            'size': volume['Size'],
            'create_time': volume['CreateTime'].isoformat(),
            'state': volume['State'],
            'availability_zone': volume['AvailabilityZone'],
            'encrypted': volume.get('Encrypted', False),
            'iops': volume.get('Iops', 0),
            'throughput': volume.get('Throughput', 0),
            'multi_attach_enabled': volume.get('MultiAttachEnabled', False),
            'monthly_cost': calculate_monthly_cost(volume['Size'], volume_type, self.region),
            'attached_instances': [],
            'Tags': volume.get('Tags', []) # 원본 Tags 정보 포함
        }

        # 태그 정보 추가
        if 'Tags' in volume:
            volume_info['tags'] = get_tags_as_dict(volume['Tags'])

            # Name 태그 따로 저장 (이름 기반 필터링 편의성)
            if 'Name' in volume_info['tags']:
                volume_info['name'] = volume_info['tags']['Name']

        # 연결된 인스턴스 정보 추가
        if volume['Attachments']:
            for attachment in volume['Attachments']:
                volume_info['attached_instances'].append({
                    'instance_id': attachment['InstanceId'],
                    'attach_time': attachment['AttachTime'].isoformat(),
                    'device': attachment['Device'],
                    'delete_on_termination': attachment.get('DeleteOnTermination', False),
                    'state': attachment['State']
                })

        # CloudWatch 메트릭 데이터 조회 및 간략화하여 추가
        full_metrics = self.get_volume_metrics(volume_id, volume_type)
        volume_info['metrics'] = self.simplify_metrics(full_metrics)

        return volume_info

    def analyze_volumes(self):
        """
        모든 볼륨에 대해 유휴 상태 및 과대 프로비저닝 상태를 분석

        :return: 분석 결과 딕셔너리
        """
        # 모든 볼륨 정보 수집
        volumes = self.get_all_ebs_volumes()

        # 유휴 상태 볼륨 감지
        idle_volumes = self.idle_detector.detect_idle_volumes(volumes)

        # 과대 프로비저닝된 볼륨 감지
        overprovisioned_volumes = self.overprovisioned_detector.detect_overprovisioned_volumes(volumes)

        # 유휴 및 과대 프로비저닝된 볼륨 ID 목록
        idle_volume_ids = [vol['volume_id'] for vol in idle_volumes]
        overprovisioned_volume_ids = [vol['volume_id'] for vol in overprovisioned_volumes]

        # 모든 볼륨의 기본 정보 포맷팅
        all_volumes = []
        for volume in volumes:
            volume_info = self.format_volume_info(volume)

            # 유휴 상태 및 과대 프로비저닝 여부 표시
            volume_info['is_idle'] = volume_info['volume_id'] in idle_volume_ids
            volume_info['is_overprovisioned'] = volume_info['volume_id'] in overprovisioned_volume_ids

            # 유휴/과대 프로비저닝 볼륨인 경우 분석 정보 추가
            if volume_info['is_idle']:
                idle_volume = next((vol for vol in idle_volumes if vol['volume_id'] == volume_info['volume_id']), None)
                if idle_volume:
                    volume_info['idle_analysis'] = {
                        'reason': idle_volume.get('idle_reason', ''),
                        'recommendation': idle_volume.get('recommendation', '')
                    }

            if volume_info['is_overprovisioned']:
                over_volume = next((vol for vol in overprovisioned_volumes if vol['volume_id'] == volume_info['volume_id']), None)
                if over_volume:
                    volume_info['overprovisioned_analysis'] = {
                        'reason': over_volume.get('reason', ''),
                        'recommendation': over_volume.get('recommendation', ''),
                        'details': {
                            'current_size': over_volume.get('current_size'),
                            'recommended_size': over_volume.get('recommended_size'),
                            'avg_disk_usage': over_volume.get('avg_disk_usage'),
                            'estimated_savings': over_volume.get('estimated_savings')
                        }
                    }

            all_volumes.append(volume_info)

        # 결과 요약
        analysis_summary = {
            'region': self.region,
            'total_volumes_analyzed': len(volumes),
            'idle_volumes_count': len(idle_volumes),
            'overprovisioned_volumes_count': len(overprovisioned_volumes),
            'idle_volumes': idle_volumes,
            'overprovisioned_volumes': overprovisioned_volumes,
            # 'all_volumes': all_volumes  # 너무 클 수 있으므로 기본적으로 포함하지 않음
        }

        return analysis_summary

    def analyze_specific_volume(self, volume_id):
        """
        특정 볼륨 ID에 대해 상세 분석을 수행합니다.

        :param volume_id: 분석할 볼륨 ID
        :return: 상세 분석 결과 딕셔너리
        """
        logger.info(f"특정 볼륨 상세 분석 시작: {volume_id} ({self.region})")
        try:
            response = self.ec2_client.describe_volumes(VolumeIds=[volume_id])
            if not response['Volumes']:
                logger.error(f"볼륨 {volume_id}을 찾을 수 없습니다.")
                return {"error": f"볼륨 {volume_id}을(를) 찾을 수 없습니다.", "status": "오류"}

            volume = response['Volumes'][0]

            # 기본 정보 포맷팅
            volume_info = self.format_volume_info(volume)

            # 유휴 상태 분석
            is_idle, idle_reason, idle_metrics = self.idle_detector.is_idle_volume(volume_id, volume_info['metrics'])
            volume_info['is_idle'] = is_idle
            volume_info['idle_check_details'] = {'reason': idle_reason, 'metrics': idle_metrics}

            # 과대 프로비저닝 분석 (is_idle_volume과 중복 호출을 피하기 위해 내부 로직 사용)
            overprovisioned_result = self.overprovisioned_detector.is_overprovisioned_volume(volume_id, volume)
            volume_info['is_overprovisioned'] = overprovisioned_result is not None
            if overprovisioned_result:
                volume_info['overprovisioned_check_details'] = overprovisioned_result

            # 최종 상태 및 권장 사항 결정
            if is_idle:
                volume_info['status'] = "Idle"
                # 권장 사항은 IdleDetector 결과 사용 (이미 생성됨)
                volume_info['recommendation'] = self.idle_detector.detect_idle_volumes([volume])[0].get('recommendation', 'Idle volume detected. Consider snapshot and delete/change type.')
            elif overprovisioned_result:
                volume_info['status'] = "Overprovisioned"
                volume_info['recommendation'] = overprovisioned_result.get('recommendation_summary', 'Volume is overprovisioned. Consider resizing or changing type.')
            else:
                volume_info['status'] = "In-use / Optimized"
                volume_info['recommendation'] = "볼륨이 현재 사용 중이며 최적화된 것으로 보입니다."

            return volume_info

        except Exception as e:
            logger.error(f"볼륨 {volume_id} 분석 중 예외 발생: {str(e)}", exc_info=True)
            return {"error": f"볼륨 분석 중 오류 발생: {str(e)}", "status": "오류"}