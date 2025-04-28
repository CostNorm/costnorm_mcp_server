import logging
import boto3
import re
import time
from datetime import datetime, timedelta
from storage.ebs.utils import calculate_monthly_cost
from botocore.exceptions import ClientError

logger = logging.getLogger()

class OverprovisionedVolumeDetector:
    """
    과대 프로비저닝된 EBS 볼륨을 감지하는 클래스
    """

    def __init__(self, region, ec2_client, cloudwatch_client, criteria):
        """
        :param region: AWS 리전
        :param ec2_client: EC2 클라이언트
        :param cloudwatch_client: CloudWatch 클라이언트
        :param criteria: 과대 프로비저닝 감지 기준
        """
        self.region = region
        self.ec2_client = ec2_client
        self.cloudwatch_client = cloudwatch_client
        self.criteria = criteria
        # SSM 클라이언트 초기화 (EC2 내부 파일시스템 정보 수집용)
        self.ssm_client = boto3.client('ssm', region_name=region)
        # 인스턴스 SSM 상태 캐시 (성능 향상을 위해)
        self.instance_ssm_status_cache = {}

    def check_instance_ssm_status(self, instance_id):
        """
        인스턴스가 SSM 명령을 실행할 수 있는 상태인지 확인

        :param instance_id: EC2 인스턴스 ID
        :return: (가능 여부, 상태 메시지)
        """
        # 캐시된 결과가 있으면 반환
        if instance_id in self.instance_ssm_status_cache:
            return self.instance_ssm_status_cache[instance_id]

        try:
            # 인스턴스 상태 확인
            ec2_response = self.ec2_client.describe_instances(InstanceIds=[instance_id])
            if not ec2_response['Reservations'] or not ec2_response['Reservations'][0]['Instances']:
                result = (False, f"인스턴스 {instance_id}를 찾을 수 없습니다.")
                self.instance_ssm_status_cache[instance_id] = result
                return result

            instance = ec2_response['Reservations'][0]['Instances'][0]
            state = instance.get('State', {}).get('Name', '')

            if state != 'running':
                result = (False, f"인스턴스 {instance_id}가 실행 중이 아닙니다(현재 상태: {state}).")
                self.instance_ssm_status_cache[instance_id] = result
                return result

            # SSM에서 관리되는 인스턴스인지 확인
            try:
                ssm_response = self.ssm_client.describe_instance_information(
                    Filters=[{'Key': 'InstanceIds', 'Values': [instance_id]}]
                )

                if not ssm_response['InstanceInformationList']:
                    result = (False, f"인스턴스 {instance_id}가 SSM에 등록되지 않았습니다. SSM Agent가 설치되어 있고 올바르게 구성되어 있는지 확인하세요.")
                    self.instance_ssm_status_cache[instance_id] = result
                    return result

                ping_status = ssm_response['InstanceInformationList'][0].get('PingStatus', '')
                if ping_status != 'Online':
                    result = (False, f"인스턴스 {instance_id}의 SSM Agent가 온라인 상태가 아닙니다(현재 상태: {ping_status}).")
                    self.instance_ssm_status_cache[instance_id] = result
                    return result

                result = (True, "인스턴스가 SSM 명령을 실행할 수 있는 상태입니다.")
                self.instance_ssm_status_cache[instance_id] = result
                return result
            except Exception as ssm_error:
                # SSM 서비스 오류(권한 부족 등)가 발생한 경우
                logger.warning(f"SSM 서비스 오류: {str(ssm_error)}")
                result = (False, f"SSM 서비스 오류: {str(ssm_error)}")
                self.instance_ssm_status_cache[instance_id] = result
                return result

        except Exception as e:
            # 권한이 없거나 다른 오류가 발생한 경우
            logger.warning(f"인스턴스 {instance_id}의 상태 확인 중 오류 발생: {str(e)}")
            result = (False, f"인스턴스 상태 확인 중 오류 발생: {str(e)}")
            self.instance_ssm_status_cache[instance_id] = result
            return result

    def get_disk_usage_metrics(self, instance_id, device_name, start_time, end_time):
        """
        CloudWatch 에이전트를 통해 수집된 디스크 사용률 지표를 가져옴

        :param instance_id: EC2 인스턴스 ID
        :param device_name: 디바이스 이름
        :param start_time: 측정 시작 시간
        :param end_time: 측정 종료 시간
        :return: 디스크 사용률 지표
        """
        # 먼저 CloudWatch 메트릭 확인
        try:
            # 인스턴스에 연결된 모든 볼륨의 CloudWatch 메트릭 확인
            metrics = self.cloudwatch_client.list_metrics(
                Namespace='CWAgent',
                MetricName='disk_used_percent',
                Dimensions=[{'Name': 'InstanceId', 'Value': instance_id}]
            )

            # CloudWatch에 메트릭이 있으면 메트릭 사용
            if metrics.get('Metrics'):
                paths = set()
                for metric in metrics['Metrics']:
                    for dim in metric['Dimensions']:
                        if dim['Name'] == 'path':
                            paths.add(dim['Value'])

                # 경로 정보 로깅
                if paths:
                    logger.info(f"인스턴스 {instance_id}에서 발견된 디스크 경로: {paths}")
                else:
                    logger.warning(f"인스턴스 {instance_id}에서 디스크 경로를 찾을 수 없습니다. 모든 차원 정보: {[metric['Dimensions'] for metric in metrics['Metrics']]}")

                # 루트 디바이스인 경우 '/' 경로 사용 시도
                device_short_name = device_name.split('/')[-1]
                if device_short_name in ['xvda', 'sda', 'nvme0n1'] or device_short_name.startswith('xvda') or device_short_name.startswith('sda'):
                    if '/' in paths:
                        logger.info(f"루트 디바이스 {device_name}에 대해 경로 '/'를 사용합니다.")
                        response = self.cloudwatch_client.get_metric_statistics(
                            Namespace='CWAgent',
                            MetricName='disk_used_percent',
                            Dimensions=[
                                {'Name': 'InstanceId', 'Value': instance_id},
                                {'Name': 'path', 'Value': '/'}
                            ],
                            StartTime=start_time,
                            EndTime=end_time,
                            Period=86400,
                            Statistics=['Average']
                        )

                        if response['Datapoints']:
                            return response['Datapoints']

                # 가장 적합한 경로 찾기 시도
                fs_path = self.estimate_filesystem_path(device_name, paths)

                if fs_path:
                    logger.info(f"디바이스 {device_name}에 대해 추정된 경로: {fs_path}")

                    response = self.cloudwatch_client.get_metric_statistics(
                        Namespace='CWAgent',
                        MetricName='disk_used_percent',
                        Dimensions=[
                            {'Name': 'InstanceId', 'Value': instance_id},
                            {'Name': 'path', 'Value': fs_path}
                        ],
                        StartTime=start_time,
                        EndTime=end_time,
                        Period=86400,  # 1일 단위
                        Statistics=['Average']
                    )

                    if response['Datapoints']:
                        return response['Datapoints']

            # 기타 모든 방법을 시도 후 실패하면 직접 마운트 정보 조회
            logger.info(f"CloudWatch에서 인스턴스 {instance_id}의 디스크 사용률 메트릭을 찾을 수 없습니다. 대체 방법 사용...")

            # 디바이스가 루트 볼륨인 경우 바로 SSM 통해 루트 볼륨 확인
            device_short_name = device_name.split('/')[-1]
            if device_short_name in ['xvda', 'sda', 'nvme0n1'] or device_short_name.startswith('xvda') or device_short_name.startswith('sda'):
                logger.info(f"루트 디바이스 {device_name} 감지됨. SSM을 통해 루트 파티션 사용률을 확인합니다.")
                datapoints = self.get_root_disk_usage_via_ssm(instance_id)
                if datapoints:
                    return datapoints

            # 일반적인 SSM 경로 사용
            ssm_status, message = self.check_instance_ssm_status(instance_id)
            if ssm_status:
                # SSM을 통해 디스크 사용률 조회 시도
                return self.get_disk_usage_via_ssm(instance_id, device_name)
            else:
                logger.warning(f"SSM을 사용할 수 없습니다: {message}. 추정치 사용...")
                return self.get_estimated_disk_usage(instance_id, device_name)

        except Exception as e:
            logger.error(f"CloudWatch 메트릭 조회 중 오류 발생: {str(e)}", exc_info=True)
            # 오류 발생 시 추정 데이터 사용
            return self.get_estimated_disk_usage(instance_id, device_name)

    def estimate_filesystem_path(self, device_name, available_paths):
        """
        디바이스 이름과 사용 가능한 경로 목록을 기반으로 가장 적합한 경로 추정

        :param device_name: 디바이스 이름
        :param available_paths: 사용 가능한 경로 목록
        :return: 추정된 경로 또는 None
        """
        # 디바이스 이름에서 짧은 이름 추출 (예: /dev/sda1 -> sda1)
        short_name = device_name.split('/')[-1]

        # 디바이스 이름과 경로 간의 일반적인 매핑
        common_mappings = {
            'xvda1': '/', 'sda1': '/',  # 루트 볼륨
            'xvdf': '/data', 'sdf': '/data',  # 데이터 볼륨
            'xvdg': '/mnt', 'sdg': '/mnt',  # 마운트 볼륨
        }

        # 1. 디바이스 이름으로 직접 매핑이 있으면 해당 경로 반환
        if short_name in common_mappings and common_mappings[short_name] in available_paths:
            return common_mappings[short_name]

        # 2. 루트 볼륨의 경우 '/'를 반환
        if re.match(r'xvda\d*|sda\d*|nvme0n1p\d*', short_name) and '/' in available_paths:
            return '/'

        # 3. 데이터 볼륨의 경우 일반적인 데이터 경로 찾기
        data_paths = [p for p in available_paths if p.startswith('/data') or p.startswith('/mnt')]
        if data_paths:
            return data_paths[0]

        # 4. '/' 외의 가장 짧은 경로 반환 (일반적으로 주요 볼륨)
        non_root_paths = [p for p in available_paths if p != '/']
        if non_root_paths:
            return min(non_root_paths, key=len)

        # 5. 마지막 수단으로 '/' 반환
        if '/' in available_paths:
            return '/'

        # 적합한 경로를 찾지 못한 경우
        return None

    def get_disk_usage_via_ssm(self, instance_id, device_name):
        """
        SSM을 통해 디스크 사용률 조회 (인스턴스가 SSM을 지원하는지 미리 확인해야 함)

        :param instance_id: EC2 인스턴스 ID
        :param device_name: 디바이스 이름
        :return: 디스크 사용률 데이터
        """
        try:
            # 먼저 파일시스템 경로 조회
            fs_path = self.get_filesystem_path_safe(instance_id, device_name)

            if not fs_path:
                logger.warning(f"인스턴스 {instance_id}의 디바이스 {device_name}에 대한 파일시스템 경로를 찾을 수 없습니다.")
                return None

            logger.info(f"인스턴스 {instance_id}, 디바이스 {device_name}의 파일시스템 경로: {fs_path}. df -h 실행...")
            command = f"df -h {fs_path} | tail -n 1"

            # SSM 명령 실행
            response = self.ssm_client.send_command(
                InstanceIds=[instance_id],
                DocumentName="AWS-RunShellScript",
                Parameters={'commands': [command]},
                TimeoutSeconds=60
            )

            command_id = response['Command']['CommandId']
            logger.info(f"SSM 명령 전송됨 (Command ID: {command_id})")

            # 명령 결과 대기
            output = None
            start_wait = time.time()
            while time.time() - start_wait < 120: # 최대 2분 대기
                try:
                    command_invocation = self.ssm_client.get_command_invocation(
                        CommandId=command_id,
                        InstanceId=instance_id
                    )

                    status = command_invocation['Status']
                    if status == 'Success':
                        output = command_invocation['StandardOutputContent']
                        break
                    elif status in ['Pending', 'InProgress', 'Delayed']:
                        time.sleep(5)
                    else:
                        logger.error(f"SSM 명령 실패 (Command ID: {command_id}, Status: {status}): {command_invocation.get('StandardErrorContent')}")
                        return None
                except ClientError as ce:
                    # get_command_invocation 호출 중 오류 발생 가능성
                    logger.error(f"SSM 명령 결과 조회 중 오류 (Command ID: {command_id}): {str(ce)}")
                    return None
            else:
                logger.error(f"SSM 명령 시간 초과 (Command ID: {command_id})")
                return None

            if not output:
                 logger.warning(f"SSM 명령 결과가 비어있습니다 (Command ID: {command_id})")
                 return None

            # 결과 파싱 (예: Filesystem Size Used Avail Use% Mounted on)
            # /dev/xvda1      20G  1.5G   19G   8% /
            match = re.search(r'(\d+)%\s+\S+$', output)
            if match:
                usage_percent = int(match.group(1))
                logger.info(f"SSM 결과 파싱 성공: {usage_percent}%")
                # CloudWatch Datapoints 형식으로 변환
                return [{'Timestamp': datetime.now(), 'Average': float(usage_percent)}]
            else:
                logger.warning(f"SSM 명령 결과 파싱 실패: {output}")
                return None

        except Exception as e:
            logger.error(f"SSM을 통한 디스크 사용률 조회 중 오류: {str(e)}", exc_info=True)
            return None

    def get_estimated_disk_usage(self, instance_id, device_name):
        """
        SSM 또는 CloudWatch Agent 사용 불가 시 추정 디스크 사용률 반환
        """
        # 단순 추정: 20% 사용으로 가정
        # 더 나은 추정을 위해 인스턴스 유형, OS 등을 고려할 수 있음
        logger.warning(f"인스턴스 {instance_id}의 디스크 {device_name} 사용률을 측정할 수 없어 20%로 추정합니다.")
        return [{'Timestamp': datetime.now(), 'Average': 20.0}]

    def get_filesystem_path_safe(self, instance_id, device_name):
        """
        파일시스템 정보 조회 실패 시 기본 경로 반환
        """
        try:
            info = self.get_filesystem_info(instance_id, device_name)
            if info:
                return info['path']
            else:
                return self.get_default_filesystem_path(device_name)
        except Exception:
            return self.get_default_filesystem_path(device_name)

    def get_filesystem_info(self, instance_id, device_name):
        """
        SSM을 사용하여 디스크의 마운트 경로 및 파일시스템 유형 조회

        :param instance_id: EC2 인스턴스 ID
        :param device_name: 디바이스 이름 (예: /dev/xvda1)
        :return: 파일시스템 정보 딕셔너리 {'path': '/mnt/data', 'type': 'xfs'} 또는 None
        """
        # 장치 이름에 /dev/ 접두사가 없으면 추가
        if not device_name.startswith('/dev/'):
            device_name_full = f"/dev/{device_name}"
        else:
            device_name_full = device_name

        # 명령: lsblk -no NAME,MOUNTPOINT,FSTYPE /dev/device
        # 또는 df -P /dev/device | tail -n 1 | awk '{print $6}' 로 경로만 가져올 수 있음
        command = f"lsblk -no MOUNTPOINT,FSTYPE {device_name_full} | grep -vE '^\s*$' | head -n 1"

        try:
            response = self.ssm_client.send_command(
                InstanceIds=[instance_id],
                DocumentName="AWS-RunShellScript",
                Parameters={'commands': [command]},
                TimeoutSeconds=60
            )
            command_id = response['Command']['CommandId']

            # 결과 대기
            output = None
            start_wait = time.time()
            while time.time() - start_wait < 120:
                try:
                    invocation = self.ssm_client.get_command_invocation(
                        CommandId=command_id, InstanceId=instance_id
                    )
                    status = invocation['Status']
                    if status == 'Success':
                        output = invocation['StandardOutputContent'].strip()
                        break
                    elif status in ['Pending', 'InProgress', 'Delayed']:
                        time.sleep(5)
                    else:
                        logger.error(f"SSM 파일시스템 정보 조회 실패 (Command ID: {command_id}, Status: {status}): {invocation.get('StandardErrorContent')}")
                        return None
                except ClientError as ce:
                    logger.error(f"SSM 파일시스템 정보 결과 조회 중 오류 (Command ID: {command_id}): {str(ce)}")
                    return None
            else:
                logger.error(f"SSM 파일시스템 정보 조회 시간 초과 (Command ID: {command_id})")
                return None

            if not output:
                logger.info(f"인스턴스 {instance_id}의 디바이스 {device_name}에 대한 파일시스템 정보 없음 (출력 없음)")
                # 루트 볼륨인 경우 '/'로 추정
                if self.is_likely_root_device(device_name):
                    return {'path': '/', 'type': 'unknown'}
                return None

            # 결과 파싱 (예: "/ xfs" 또는 "/mnt/data ext4")
            parts = output.split()
            if len(parts) >= 1:
                mount_point = parts[0]
                fs_type = parts[1] if len(parts) > 1 else 'unknown'

                # 마운트되지 않은 경우 (예: [SWAP]) 필터링
                if mount_point == '[SWAP]':
                    logger.info(f"디바이스 {device_name}는 SWAP 파티션입니다.")
                    return None

                logger.info(f"파일시스템 정보 파싱 성공: Path={mount_point}, Type={fs_type}")
                return {'path': mount_point, 'type': fs_type}
            else:
                logger.warning(f"SSM 파일시스템 정보 결과 파싱 실패: {output}")
                # 루트 볼륨인 경우 '/'로 추정
                if self.is_likely_root_device(device_name):
                    return {'path': '/', 'type': 'unknown'}
                return None

        except Exception as e:
            logger.error(f"SSM 파일시스템 정보 조회 중 오류: {str(e)}", exc_info=True)
            # 오류 발생 시 루트 볼륨이면 '/' 추정
            if self.is_likely_root_device(device_name):
                 return {'path': '/', 'type': 'unknown'}
            return None

    def get_default_filesystem_path(self, device_name):
        """
        디바이스 이름을 기반으로 기본 파일시스템 경로 추정
        """
        # 루트 볼륨 추정 (xvda, sda, nvme0n1)
        if self.is_likely_root_device(device_name):
            return '/'
        # 다른 볼륨은 /data 또는 /mnt 로 추정
        elif 'xvdf' in device_name or 'sdf' in device_name:
            return '/data'
        else:
            return '/mnt'

    def is_likely_root_device(self, device_name):
        """
        디바이스 이름이 루트 디바이스일 가능성이 높은지 판단
        """
        short_name = device_name.split('/')[-1]
        return re.match(r'xvda\d*|sda\d*|nvme0n1(p\d+)?$', short_name) is not None

    def get_root_disk_usage_via_ssm(self, instance_id):
        """
        SSM을 통해 루트(/) 디스크 사용률을 조회
        """
        try:
            command = "df -h / | tail -n 1 | awk '{print $5}' | sed 's/%//'"
            response = self.ssm_client.send_command(
                InstanceIds=[instance_id],
                DocumentName="AWS-RunShellScript",
                Parameters={'commands': [command]},
                TimeoutSeconds=60
            )
            command_id = response['Command']['CommandId']
            logger.info(f"루트 디스크 사용률 SSM 명령 전송됨 (Command ID: {command_id})")

            output = None
            start_wait = time.time()
            while time.time() - start_wait < 120:
                try:
                    invocation = self.ssm_client.get_command_invocation(
                        CommandId=command_id, InstanceId=instance_id
                    )
                    status = invocation['Status']
                    if status == 'Success':
                        output = invocation['StandardOutputContent'].strip()
                        break
                    elif status in ['Pending', 'InProgress', 'Delayed']:
                        time.sleep(5)
                    else:
                        logger.error(f"루트 디스크 사용률 SSM 명령 실패 (Command ID: {command_id}, Status: {status}): {invocation.get('StandardErrorContent')}")
                        return None
                except ClientError as ce:
                    logger.error(f"루트 디스크 사용률 SSM 결과 조회 중 오류 (Command ID: {command_id}): {str(ce)}")
                    return None
            else:
                logger.error(f"루트 디스크 사용률 SSM 명령 시간 초과 (Command ID: {command_id})")
                return None

            if output and output.isdigit():
                usage_percent = int(output)
                logger.info(f"루트 디스크 사용률 SSM 결과 파싱 성공: {usage_percent}%")
                return [{'Timestamp': datetime.now(), 'Average': float(usage_percent)}]
            else:
                logger.warning(f"루트 디스크 사용률 SSM 결과 파싱 실패: {output}")
                return None

        except Exception as e:
            logger.error(f"루트 디스크 사용률 SSM 조회 중 오류: {str(e)}", exc_info=True)
            return None

    def is_overprovisioned(self, usage_datapoints):
        """
        디스크 사용률 데이터를 기반으로 과대 프로비저닝 여부 판단

        :param usage_datapoints: 디스크 사용률 데이터포인트 리스트
        :return: 과대 프로비저닝 여부, 평균 사용률, 판단 근거
        """
        if not usage_datapoints:
            return False, 0, "디스크 사용률 데이터를 가져올 수 없습니다."

        # 평균 사용률 계산
        avg_usage = sum(dp['Average'] for dp in usage_datapoints) / len(usage_datapoints)

        # 기준과 비교
        if avg_usage < self.criteria['disk_usage_threshold']:
            reason = f"평균 디스크 사용률({avg_usage:.2f}%)이 임계값({self.criteria['disk_usage_threshold']}%) 미만입니다."
            return True, avg_usage, reason
        else:
            reason = f"평균 디스크 사용률({avg_usage:.2f}%)이 임계값({self.criteria['disk_usage_threshold']}%) 이상입니다."
            return False, avg_usage, reason

    def detect_overprovisioned_volumes(self, volumes):
        """
        과대 프로비저닝된 볼륨을 감지

        :param volumes: 분석할 볼륨 목록
        :return: 과대 프로비저닝된 볼륨 정보 리스트
        """
        overprovisioned_volumes = []
        end_time = datetime.now()
        start_time = end_time - timedelta(days=self.criteria['days_to_check'])

        for volume in volumes:
            volume_id = volume['VolumeId']
            volume_size = volume['Size']
            volume_type = volume['VolumeType']

            # gp2 볼륨은 제외 (최적화가 복잡할 수 있음)
            if volume_type == 'gp2':
                logger.info(f"{volume_id} (gp2)는 과대 프로비저닝 분석에서 제외됩니다.")
                continue

            # 볼륨이 연결된 인스턴스 정보 확인
            attachments = volume.get('Attachments', [])
            if not attachments:
                logger.info(f"{volume_id} 볼륨이 어떤 인스턴스에도 연결되어 있지 않아 과대 프로비저닝 분석에서 제외됩니다.")
                continue

            # 첫 번째 연결된 인스턴스 정보 사용
            instance_id = attachments[0].get('InstanceId')
            device_name = attachments[0].get('Device')

            if not instance_id or not device_name:
                logger.warning(f"{volume_id} 볼륨의 연결 정보가 불완전합니다. 분석 건너뜀.")
                continue

            logger.info(f"{volume_id} 볼륨 과대 프로비저닝 분석 중 (인스턴스: {instance_id}, 디바이스: {device_name})...")

            # 디스크 사용률 데이터 가져오기
            disk_usage_datapoints = self.get_disk_usage_metrics(instance_id, device_name, start_time, end_time)

            # 과대 프로비저닝 여부 판단
            is_over, avg_usage, reason = self.is_overprovisioned(disk_usage_datapoints)

            if is_over:
                # 과대 프로비저닝된 볼륨 정보 저장
                logger.info(f"{volume_id} 볼륨이 과대 프로비저닝된 것으로 감지됨: {reason}")

                # 권장 크기 계산 (디스크 사용률 기반)
                recommended_size = self.recommend_volume_size(avg_usage, volume_size)

                # 절감액 계산
                current_cost = calculate_monthly_cost(volume_size, volume_type, self.region)
                recommended_cost = calculate_monthly_cost(recommended_size, volume_type, self.region)
                estimated_savings = max(0, current_cost - recommended_cost)

                volume_info = {
                    'volume_id': volume_id,
                    'volume_type': volume_type,
                    'current_size': volume_size,
                    'recommended_size': recommended_size,
                    'avg_disk_usage': avg_usage,
                    'reason': reason,
                    'instance_id': instance_id,
                    'device_name': device_name,
                    'current_monthly_cost': current_cost,
                    'recommended_monthly_cost': recommended_cost,
                    'estimated_savings': estimated_savings,
                    'recommendation': f"볼륨 크기를 {recommended_size}GB로 줄이는 것을 권장합니다 (현재: {volume_size}GB). 예상 절감액: ${estimated_savings:.2f}/월."
                }
                overprovisioned_volumes.append(volume_info)
            else:
                 logger.info(f"{volume_id} 볼륨은 과대 프로비저닝되지 않았습니다: {reason}")

        return overprovisioned_volumes

    def recommend_volume_size(self, avg_usage, current_size):
        """
        평균 디스크 사용률을 기반으로 권장 볼륨 크기를 계산합니다.

        :param avg_usage: 평균 디스크 사용률 (%)
        :param current_size: 현재 볼륨 크기 (GB)
        :return: 권장 볼륨 크기 (GB)
        """
        # 사용된 공간 계산 (GB)
        used_space_gb = current_size * (avg_usage / 100.0)

        # 버퍼 추가 (예: 사용 공간의 30% 또는 최소 10GB)
        buffer_gb = max(used_space_gb * self.criteria.get('resize_buffer_percent', 0.3), self.criteria.get('resize_min_buffer_gb', 10))

        # 권장 크기 계산 (GB)
        recommended_size_gb = used_space_gb + buffer_gb

        # 가장 가까운 GB 단위로 올림
        import math
        recommended_size_gb = math.ceil(recommended_size_gb)

        # AWS 최소 볼륨 크기(1GB) 보장
        recommended_size_gb = max(1, recommended_size_gb)

        # 현재 크기보다 커지지 않도록 함
        recommended_size_gb = min(current_size, recommended_size_gb)

        logger.info(f"권장 크기 계산: 현재={current_size}GB, 사용률={avg_usage:.2f}%, 사용량={used_space_gb:.2f}GB, 버퍼={buffer_gb:.2f}GB => 권장={recommended_size_gb}GB")
        return recommended_size_gb

    def get_performance_metrics(self, volume_id, start_time, end_time):
        """
        볼륨 성능 관련 메트릭(IOPS, Throughput) 수집
        """
        metrics = {}
        performance_metrics = [
            'VolumeReadOps', 'VolumeWriteOps',
            'VolumeReadBytes', 'VolumeWriteBytes',
            'VolumeTotalReadTime', 'VolumeTotalWriteTime',
            'VolumeQueueLength'
        ]

        for metric_name in performance_metrics:
            try:
                response = self.cloudwatch_client.get_metric_statistics(
                    Namespace='AWS/EBS',
                    MetricName=metric_name,
                    Dimensions=[{'Name': 'VolumeId', 'Value': volume_id}],
                    StartTime=start_time,
                    EndTime=end_time,
                    Period=300, # 5분 단위
                    Statistics=['Maximum', 'Average']
                )
                if response['Datapoints']:
                    # 최대값과 평균값 저장
                    max_val = max(dp['Maximum'] for dp in response['Datapoints'])
                    avg_val = sum(dp['Average'] for dp in response['Datapoints']) / len(response['Datapoints'])
                    unit = response['Datapoints'][0]['Unit']
                    metrics[metric_name] = {'max': max_val, 'avg': avg_val, 'unit': unit}
            except Exception as e:
                logger.warning(f"볼륨 {volume_id}의 {metric_name} 성능 메트릭 조회 중 오류: {str(e)}")

        # IOPS 계산 (최대값 기준)
        max_read_ops = metrics.get('VolumeReadOps', {}).get('max', 0)
        max_write_ops = metrics.get('VolumeWriteOps', {}).get('max', 0)
        metrics['PeakIOPS'] = (max_read_ops + max_write_ops) / 300 # 초당 IOPS

        # Throughput 계산 (최대값 기준, MiB/s)
        max_read_bytes = metrics.get('VolumeReadBytes', {}).get('max', 0)
        max_write_bytes = metrics.get('VolumeWriteBytes', {}).get('max', 0)
        peak_throughput_mib = (max_read_bytes + max_write_bytes) / 300 / (1024 * 1024)
        metrics['PeakThroughputMiBps'] = peak_throughput_mib

        return metrics

    def is_overprovisioned_volume(self, volume_id, volume):
        """
        볼륨이 과대 프로비저닝되었는지 종합적으로 판단

        :param volume_id: 볼륨 ID
        :param volume: 볼륨 상세 정보 (EC2 API 응답)
        :return: 과대 프로비저닝 정보 딕셔너리 또는 None
        """
        volume_size = volume['Size']
        volume_type = volume['VolumeType']

        # gp2는 일단 제외
        if volume_type == 'gp2':
            return None

        attachments = volume.get('Attachments', [])
        if not attachments:
            return None # 연결되지 않은 볼륨은 유휴 볼륨으로 처리

        instance_id = attachments[0].get('InstanceId')
        device_name = attachments[0].get('Device')

        if not instance_id or not device_name:
            return None

        end_time = datetime.now()
        start_time = end_time - timedelta(days=self.criteria['days_to_check'])

        # 1. 디스크 사용률 확인
        disk_usage_datapoints = self.get_disk_usage_metrics(instance_id, device_name, start_time, end_time)
        is_over_size, avg_usage, size_reason = self.is_overprovisioned(disk_usage_datapoints)

        # 2. 성능 메트릭 확인 (IOPS, Throughput)
        performance_metrics = self.get_performance_metrics(volume_id, start_time, end_time)
        peak_iops = performance_metrics.get('PeakIOPS', 0)
        peak_throughput = performance_metrics.get('PeakThroughputMiBps', 0)

        # 현재 볼륨의 프로비저닝된 성능 가져오기
        provisioned_iops = volume.get('Iops', 0)
        provisioned_throughput = volume.get('Throughput', 0) # MiB/s 아님, MB/s 일 수 있음. 확인 필요.

        # IOPS 과대 프로비저닝 확인 (gp3, io1, io2)
        is_over_iops = False
        iops_reason = ""
        if volume_type in ['gp3', 'io1', 'io2'] and provisioned_iops > 0:
             # IOPS 사용률 계산 (예: 최대 사용량의 1.5배 < 프로비저닝된 IOPS)
            if peak_iops * 1.5 < provisioned_iops * self.criteria.get('iops_usage_threshold_percent', 0.5):
                is_over_iops = True
                iops_reason = f"최대 IOPS({peak_iops:.0f})가 프로비저닝된 IOPS({provisioned_iops})의 50% 미만입니다."

        # Throughput 과대 프로비저닝 확인 (gp3)
        is_over_throughput = False
        throughput_reason = ""
        if volume_type == 'gp3' and provisioned_throughput > 0:
            # Throughput 사용률 계산 (MiB/s 단위 통일 필요)
            # describe_volumes의 Throughput 단위는 MB/s? 확인 필요
            # 여기서는 같다고 가정
            if peak_throughput * 1.5 < provisioned_throughput * self.criteria.get('throughput_usage_threshold_percent', 0.5):
                is_over_throughput = True
                throughput_reason = f"최대 처리량({peak_throughput:.1f} MiB/s)이 프로비저닝된 처리량({provisioned_throughput} MiB/s?)의 50% 미만입니다."

        # 종합 판단
        if is_over_size or is_over_iops or is_over_throughput:
            logger.info(f"{volume_id} 볼륨이 과대 프로비저닝됨 (크기: {is_over_size}, IOPS: {is_over_iops}, 처리량: {is_over_throughput}) ")

            # 권장 사항 생성
            recommendations = []
            recommended_size = volume_size
            recommended_type = volume_type
            recommended_iops = provisioned_iops
            recommended_throughput = provisioned_throughput

            if is_over_size:
                recommended_size = self.recommend_volume_size(avg_usage, current_size=volume_size)
                if recommended_size < volume_size:
                    recommendations.append(f"크기를 {recommended_size}GB로 줄입니다 (현재: {volume_size}GB).")
                else:
                     is_over_size = False # 크기 줄일 필요 없으면 False 로 변경

            # TODO: IOPS 및 처리량 기반 권장 사항 추가

            # TODO: 타입 변경 권장 (예: io1/io2 -> gp3)

            # 절감액 계산 (개선 필요)
            current_cost = calculate_monthly_cost(volume_size, volume_type, self.region)
            # TODO: 권장 스펙으로 비용 계산
            recommended_cost = calculate_monthly_cost(recommended_size, recommended_type, self.region)
            estimated_savings = max(0, current_cost - recommended_cost)

            return {
                'volume_id': volume_id,
                'is_overprovisioned': True,
                'reasons': [r for r in [size_reason, iops_reason, throughput_reason] if r],
                'recommendation_summary': " ".join(recommendations) if recommendations else "최적화 필요.",
                'details': {
                    'current_spec': {'size': volume_size, 'type': volume_type, 'iops': provisioned_iops, 'throughput': provisioned_throughput},
                    'usage': {'avg_disk_percent': avg_usage, 'peak_iops': peak_iops, 'peak_throughput_mibps': peak_throughput},
                    'recommended_spec': {'size': recommended_size, 'type': recommended_type, 'iops': recommended_iops, 'throughput': recommended_throughput},
                    'estimated_savings': estimated_savings
                }
            }

        return None 