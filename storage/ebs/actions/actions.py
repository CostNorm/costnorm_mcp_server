import boto3
import logging
import time
from datetime import datetime
from botocore.exceptions import ClientError

logger = logging.getLogger()

class EBSActionExecutor:
    """
    EBS 볼륨에 대한 실제 조치(액션)를 수행하는 클래스
    분석 결과에 따른 권장 조치를 실행합니다.
    """

    def __init__(self, region):
        """
        :param region: AWS 리전
        """
        self.region = region
        self.ec2_client = boto3.client('ec2', region_name=region)

    def create_snapshot(self, volume_id, description=None, tags=None):
        """
        EBS 볼륨의 스냅샷을 생성합니다.
        스냅샷 생성은 비동기적으로 처리됩니다 - 생성 요청만 전송하고 완료를 기다리지 않습니다.

        :param volume_id: 스냅샷을 생성할 볼륨 ID
        :param description: 스냅샷 설명 (기본값: None)
        :param tags: 스냅샷에 적용할 태그 딕셔너리 (기본값: None)
        :return: 생성된 스냅샷 ID 또는 None (실패 시)
        """
        try:
            # 스냅샷 생성 요청 구성
            create_args = {'VolumeId': volume_id}

            if description:
                create_args['Description'] = description

            # 태그 변환
            if tags:
                tag_specs = [{
                    'ResourceType': 'snapshot',
                    'Tags': [{'Key': k, 'Value': v} for k, v in tags.items()]
                }]
                create_args['TagSpecifications'] = tag_specs

            logger.info(f"볼륨 {volume_id}의 스냅샷 생성 시작")
            response = self.ec2_client.create_snapshot(**create_args)

            snapshot_id = response.get('SnapshotId')
            logger.info(f"볼륨 {volume_id}의 스냅샷 {snapshot_id} 생성 요청 완료. 스냅샷 생성은 백그라운드에서 계속됩니다.")

            # 스냅샷 생성이 시작되었는지만 확인하고 더 기다리지 않음
            try:
                self.ec2_client.describe_snapshots(SnapshotIds=[snapshot_id])
                logger.info(f"스냅샷 {snapshot_id} 생성 요청 확인됨. 완료까지 시간이 소요될 수 있습니다.")
            except Exception as e:
                logger.warning(f"스냅샷 {snapshot_id} 상태 확인 중 문제 발생: {str(e)}")

            return snapshot_id

        except ClientError as e:
            logger.error(f"스냅샷 생성 중 오류 발생: {str(e)}")
            return None

    def detach_volume(self, volume_id, force=False):
        """
        EBS 볼륨을 인스턴스에서 분리합니다.
        분리 요청만 보내고 완료를 기다리지 않습니다.

        :param volume_id: 분리할 볼륨 ID
        :param force: 강제 분리 여부 (기본값: False)
        :return: 성공 여부 (boolean)
        """
        try:
            # 볼륨 정보 가져오기
            response = self.ec2_client.describe_volumes(VolumeIds=[volume_id])

            if not response['Volumes']:
                logger.error(f"볼륨 {volume_id}을 찾을 수 없습니다.")
                return False

            volume = response['Volumes'][0]
            attachments = volume.get('Attachments', [])

            # 연결된 인스턴스가 없으면 성공으로 처리
            if not attachments:
                logger.info(f"볼륨 {volume_id}이 어떤 인스턴스에도 연결되어 있지 않습니다.")
                return True

            # 각 연결에 대해 분리 실행
            for attachment in attachments:
                instance_id = attachment.get('InstanceId')
                device = attachment.get('Device')

                logger.info(f"볼륨 {volume_id}을 인스턴스 {instance_id}의 {device}에서 분리 시도")

                detach_args = {'VolumeId': volume_id}

                if force:
                    detach_args['Force'] = True

                self.ec2_client.detach_volume(**detach_args)
                logger.info(f"볼륨 {volume_id} 분리 요청 완료. 분리는 백그라운드에서 계속됩니다.")

            return True

        except ClientError as e:
            logger.error(f"볼륨 분리 중 오류 발생: {str(e)}")
            return False

    def attach_volume(self, volume_id, instance_id, device):
        """
        EBS 볼륨을 인스턴스에 연결합니다.
        연결 요청을 보내고 완료되기를 기다리지 않습니다.

        :param volume_id: 연결할 볼륨 ID
        :param instance_id: 인스턴스 ID
        :param device: 디바이스 이름
        :return: 성공 여부 (boolean)
        """
        try:
            # 볼륨 상태 확인
            response = self.ec2_client.describe_volumes(VolumeIds=[volume_id])

            if not response['Volumes']:
                logger.error(f"볼륨 {volume_id}을 찾을 수 없습니다.")
                return False

            if response['Volumes'][0]['State'] != 'available':
                logger.error(f"볼륨 {volume_id}의 상태가 'available'이 아닙니다: {response['Volumes'][0]['State']}")
                return False

            # 인스턴스 상태 확인
            instance_response = self.ec2_client.describe_instances(InstanceIds=[instance_id])

            if not instance_response['Reservations'] or not instance_response['Reservations'][0]['Instances']:
                logger.error(f"인스턴스 {instance_id}를 찾을 수 없습니다.")
                return False

            instance_state = instance_response['Reservations'][0]['Instances'][0]['State']['Name']

            if instance_state != 'running':
                logger.error(f"인스턴스 {instance_id}가 'running' 상태가 아닙니다: {instance_state}")
                return False

            # 볼륨 연결
            logger.info(f"볼륨 {volume_id}를 인스턴스 {instance_id}에 연결합니다. (디바이스: {device})")

            self.ec2_client.attach_volume(
                VolumeId=volume_id,
                InstanceId=instance_id,
                Device=device
            )

            logger.info(f"볼륨 {volume_id}의 인스턴스 {instance_id} 연결 요청 완료. 연결은 백그라운드에서 계속됩니다.")
            return True

        except ClientError as e:
            logger.error(f"볼륨 연결 중 오류 발생: {str(e)}")
            return False

    def delete_volume(self, volume_id):
        """
        EBS 볼륨을 삭제합니다.
        삭제 요청만 보내고 완료를 기다리지 않습니다.

        :param volume_id: 삭제할 볼륨 ID
        :return: 성공 여부 (boolean)
        """
        try:
            # 볼륨 정보 확인 (볼륨이 존재하는지, 연결되지 않았는지 확인)
            try:
                response = self.ec2_client.describe_volumes(VolumeIds=[volume_id])

                if not response['Volumes']:
                    logger.error(f"볼륨 {volume_id}을 찾을 수 없습니다.")
                    return False

                if response['Volumes'][0]['Attachments']:
                    logger.warning(f"볼륨 {volume_id}가 아직 인스턴스에 연결되어 있습니다. 분리 후 삭제해야 합니다.")
                    # 여기서는 경고만 하고 계속 진행, AWS API가 알아서 오류를 반환할 것임
            except Exception as e:
                logger.error(f"볼륨 {volume_id} 정보 조회 중 오류: {str(e)}")
                return False

            logger.info(f"볼륨 {volume_id} 삭제 시작")
            self.ec2_client.delete_volume(VolumeId=volume_id)
            logger.info(f"볼륨 {volume_id} 삭제 요청 완료. 삭제는 백그라운드에서 계속됩니다.")

            # 바로 성공으로 처리
            return True

        except ClientError as e:
            logger.error(f"볼륨 삭제 중 오류 발생: {str(e)}")
            return False

    def modify_volume_type(self, volume_id, target_type, iops=None, throughput=None):
        """
        볼륨 유형을 변경합니다.

        :param volume_id: 볼륨 ID
        :param target_type: 대상 볼륨 타입
        :param iops: IOPS 값 (io1, io2, gp3 타입에만 필요)
        :param throughput: 처리량 (gp3 타입에만 필요)
        :return: 성공 여부 및 결과 정보
        """
        try:
            logger.info(f"볼륨 {volume_id}의 타입을 {target_type}으로 변경 시작")

            # 현재 볼륨 정보 가져오기
            current_volume = self._get_volume_info(volume_id)
            if not current_volume:
                return {'success': False, 'error': f"볼륨 {volume_id} 정보를 가져올 수 없습니다."}

            current_type = current_volume.get('VolumeType')

            if current_type == target_type:
                return {'success': True, 'message': f"볼륨 {volume_id}이 이미 요청한 타입({target_type})입니다."}

            # 변경 요청 준비
            modify_args = {
                'VolumeId': volume_id,
                'VolumeType': target_type
            }

            # 볼륨 타입에 따른 추가 파라미터 설정
            if target_type in ['io1', 'io2']:
                # io1/io2는 IOPS 필요
                modify_args['Iops'] = iops if iops is not None else 100

            elif target_type == 'gp3':
                # gp3는 IOPS와 Throughput 필요
                modify_args['Iops'] = iops if iops is not None else 3000
                modify_args['Throughput'] = throughput if throughput is not None else 125

            # 변경 요청
            response = self.ec2_client.modify_volume(**modify_args)

            # 변경 상태 확인
            modification = response.get('VolumeModification', {})
            start_state = modification.get('ModificationState')

            logger.info(f"볼륨 타입 변경 요청 완료. 변경은 백그라운드에서 계속됩니다.")

            return {
                'success': True,
                'message': f"볼륨 타입 변경 요청 성공: {current_type} -> {target_type}",
                'initial_state': start_state,
                'modification_id': modification.get('ModificationId')
            }

        except ClientError as e:
            logger.error(f"볼륨 타입 변경 중 오류 발생: {str(e)}")
            return {'success': False, 'error': str(e)}

    def modify_volume_size(self, volume_id, target_size):
        """
        볼륨 크기를 변경합니다. (주의: 크기 축소는 지원되지 않음)

        :param volume_id: 볼륨 ID
        :param target_size: 대상 크기 (GB)
        :return: 성공 여부 및 결과 정보
        """
        try:
            logger.info(f"볼륨 {volume_id}의 크기를 {target_size}GB로 변경 시작")

            # 현재 볼륨 정보 가져오기
            current_volume = self._get_volume_info(volume_id)
            if not current_volume:
                return {'success': False, 'error': f"볼륨 {volume_id} 정보를 가져올 수 없습니다."}

            current_size = current_volume.get('Size')

            if current_size == target_size:
                return {'success': True, 'message': f"볼륨 {volume_id}이 이미 요청한 크기({target_size}GB)입니다."}

            # AWS EBS는 직접적인 크기 축소를 지원하지 않음
            if target_size < current_size:
                logger.warning(f"볼륨 {volume_id}의 크기 축소({current_size}GB -> {target_size}GB)는 지원되지 않습니다.")
                return {'success': False, 'error': "볼륨 크기 축소는 지원되지 않습니다."}

            # 변경 요청 준비
            modify_args = {
                'VolumeId': volume_id,
                'Size': target_size
            }

            # 변경 요청
            response = self.ec2_client.modify_volume(**modify_args)

            # 변경 상태 확인
            modification = response.get('VolumeModification', {})
            start_state = modification.get('ModificationState')

            logger.info(f"볼륨 크기 변경 요청 완료. 변경은 백그라운드에서 계속됩니다.")

            return {
                'success': True,
                'message': f"볼륨 크기 변경 요청 성공: {current_size}GB -> {target_size}GB",
                'initial_state': start_state,
                'modification_id': modification.get('ModificationId')
            }

        except ClientError as e:
            logger.error(f"볼륨 크기 변경 중 오류 발생: {str(e)}")
            return {'success': False, 'error': str(e)}

    def _get_volume_info(self, volume_id):
        """
        단일 볼륨 정보 조회
        """
        try:
            response = self.ec2_client.describe_volumes(VolumeIds=[volume_id])
            if response['Volumes']:
                return response['Volumes'][0]
        except ClientError as e:
            logger.error(f"볼륨 {volume_id} 정보 조회 중 오류: {str(e)}")
        return None

    def check_snapshot_status(self, snapshot_id):
        """
        스냅샷 상태를 확인합니다.

        :param snapshot_id: 확인할 스냅샷 ID
        :return: 스냅샷 상태 (pending, completed, error, etc.) 또는 None (오류 시)
        """
        try:
            response = self.ec2_client.describe_snapshots(SnapshotIds=[snapshot_id])
            if response['Snapshots']:
                return response['Snapshots'][0]['State']
            else:
                logger.warning(f"스냅샷 {snapshot_id}을 찾을 수 없습니다.")
                return None
        except ClientError as e:
            logger.error(f"스냅샷 {snapshot_id} 상태 확인 중 오류 발생: {str(e)}")
            return None

    def check_volume_status(self, volume_id):
        """
        볼륨 상태를 확인합니다.

        :param volume_id: 확인할 볼륨 ID
        :return: 볼륨 상태 (available, in-use, deleted, error, etc.) 또는 None (오류 시)
        """
        try:
            response = self.ec2_client.describe_volumes(VolumeIds=[volume_id])
            if response['Volumes']:
                return response['Volumes'][0]['State']
            else:
                logger.warning(f"볼륨 {volume_id}을 찾을 수 없습니다.")
                return None
        except ClientError as e:
            # 볼륨이 삭제된 경우 등 예외 처리
            if 'InvalidVolume.NotFound' in str(e):
                logger.info(f"볼륨 {volume_id}이 삭제되었습니다.")
                return 'deleted'
            logger.error(f"볼륨 {volume_id} 상태 확인 중 오류 발생: {str(e)}")
            return None

    def _is_volume_safe_to_detach(self, volume_id, instance_id):
        """
        볼륨을 안전하게 분리할 수 있는지 확인합니다. (루트 볼륨 확인)

        :param volume_id: 볼륨 ID
        :param instance_id: 인스턴스 ID
        :return: 안전하게 분리 가능 여부 (boolean)
        """
        try:
            instance_response = self.ec2_client.describe_instances(InstanceIds=[instance_id])
            if not instance_response['Reservations'] or not instance_response['Reservations'][0]['Instances']:
                logger.warning(f"볼륨 분리 전 인스턴스 {instance_id} 정보를 찾을 수 없습니다.")
                return False # 안전을 위해 분리 불가로 판단

            instance = instance_response['Reservations'][0]['Instances'][0]
            root_device_name = instance.get('RootDeviceName')

            if not root_device_name:
                logger.warning(f"인스턴스 {instance_id}의 루트 디바이스 이름을 찾을 수 없습니다.")
                return True # 루트 디바이스 정보 없으면 일단 안전하다고 가정

            for bdm in instance.get('BlockDeviceMappings', []):
                if bdm.get('DeviceName') == root_device_name:
                    if bdm.get('Ebs', {}).get('VolumeId') == volume_id:
                        logger.warning(f"볼륨 {volume_id}은 인스턴스 {instance_id}의 루트 볼륨이므로 분리할 수 없습니다.")
                        return False
            return True

        except ClientError as e:
            logger.error(f"루트 볼륨 확인 중 오류 발생: {str(e)}")
            return False # 안전을 위해 분리 불가로 판단 