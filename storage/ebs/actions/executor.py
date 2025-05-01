import logging
import json
import time
import boto3
from datetime import datetime
from .actions import EBSActionExecutor

logger = logging.getLogger()

class RecommendationExecutor:
    """
    분석 결과의 권장 조치를 실행하는 클래스
    """

    def __init__(self, region):
        """
        :param region: AWS 리전
        """
        self.region = region
        self.ebs_action_executor = EBSActionExecutor(region)
        self.execution_history = []
        self.ec2_client = boto3.client('ec2', region_name=region)

    def execute_idle_volume_recommendation(self, volume_info, action_type):
        """
        유휴 볼륨에 대한 권장 조치를 실행합니다.
        비동기적으로 처리하여 슬랙 액션이 오래 기다리지 않도록 합니다.

        :param volume_info: 볼륨 정보 딕셔너리
        :param action_type: 실행할 조치 유형 ('snapshot_and_delete', 'snapshot_only', 'change_type')
        :return: 결과 딕셔너리
        """
        volume_id = volume_info['volume_id']
        result = {
            'volume_id': volume_id,
            'action_type': action_type,
            'success': False,
            'timestamp': datetime.now().isoformat(),
            'details': {},
            'status': 'initiated' # 작업이 시작되었음을 표시
        }

        # 유효한 작업 유형 확인
        valid_idle_actions = ['snapshot_and_delete', 'snapshot_only', 'change_type', 'change_type_and_resize']
        if action_type not in valid_idle_actions:
            error_msg = f"유휴 볼륨에 지원되지 않는 작업 유형: {action_type}. 유효한 작업: {valid_idle_actions}"
            logger.error(error_msg)
            result['details']['error'] = error_msg
            return result

        # --- 루트 볼륨 보호 로직 시작 ---
        is_root = False
        if volume_info.get('attached_instances'):
            for attachment in volume_info['attached_instances']:
                instance_id = attachment.get('instance_id')
                device_name = attachment.get('device')
                if instance_id and device_name:
                    if self._is_root_volume(instance_id, device_name):
                        is_root = True
                        break # 하나라도 루트 볼륨이면 중단

        # 루트 볼륨에 대해 위험한 작업 방지
        destructive_actions = ['snapshot_and_delete', 'change_type_and_resize'] # change_type도 위험할 수 있으나, 일단 삭제/축소만 명시적 방지
        if is_root and action_type in destructive_actions:
            warning_msg = f"작업 건너뜀: 볼륨 {volume_id}은(는) 루트 볼륨이므로 '{action_type}' 작업을 자동으로 실행할 수 없습니다."
            logger.warning(warning_msg)
            result['success'] = False
            result['status'] = 'skipped_root_volume'
            result['details']['error'] = warning_msg
            self.execution_history.append(result)
            return result

        # 루트 볼륨 타입 변경도 신중해야 함 (필요시 여기에 추가 방어 로직)
        if is_root and action_type == 'change_type':
             # 일단 경고만 로깅하고 진행 (추후 정책에 따라 변경 가능)
             logger.warning(f"주의: 루트 볼륨 {volume_id}의 타입 변경({action_type})을 진행합니다. 예상치 못한 문제가 발생할 수 있습니다.")

        # --- 루트 볼륨 보호 로직 끝 ---

        logger.info(f"볼륨 {volume_id}에 대한 '{action_type}' 작업 시작 중...")

        try:
            # 1. 스냅샷 생성 (모든 작업에 공통)
            tags = {'Name': f"Idle-{volume_id}", 'AutoCreated': 'true', 'Source': 'EBS-Optimizer'}

            if 'name' in volume_info:
                tags['SourceName'] = volume_info['name']

            snapshot_id = self.ebs_action_executor.create_snapshot(
                volume_id,
                description=f"Idle volume snapshot before {action_type} - {datetime.now().strftime('%Y-%m-%d')}",
                tags=tags
            )

            if not snapshot_id:
                result['details']['error'] = "스냅샷 생성 요청 실패"
                return result

            result['details']['snapshot_id'] = snapshot_id
            logger.info(f"볼륨 {volume_id}의 스냅샷 {snapshot_id} 생성 요청 완료. 스냅샷 생성은 백그라운드에서 계속됩니다.")

            # 2. 선택한 작업 유형에 따라 실행
            if action_type == 'snapshot_and_delete':
                # 볼륨에 연결된 경우 분리
                if volume_info.get('attached_instances'):
                    for attachment in volume_info['attached_instances']:
                        instance_id = attachment['instance_id']
                        logger.info(f"볼륨 {volume_id}를 인스턴스 {instance_id}에서 분리합니다.")

                        detach_success = self.ebs_action_executor.detach_volume(volume_id)

                        if not detach_success:
                            result['details']['error'] = f"인스턴스 {instance_id}에서 볼륨 분리 요청 실패"
                            return result

                # 볼륨 삭제
                delete_success = self.ebs_action_executor.delete_volume(volume_id)

                if not delete_success:
                    result['details']['error'] = "Volume deletion request failed"
                    return result

                result['details']['action'] = "Snapshot creation and volume deletion requests completed"
                result['details']['note'] = "작업은 백그라운드에서 계속 진행됩니다. 완료까지 몇 분 소요될 수 있습니다."
                result['success'] = True

            elif action_type == 'snapshot_only':
                result['details']['action'] = "스냅샷 생성 요청 완료"
                result['details']['note'] = "스냅샷 생성은 백그라운드에서 계속 진행됩니다. 완료까지 몇 분 소요될 수 있습니다."
                result['success'] = True

            elif action_type == 'change_type':
                # 볼륨 타입 변경 로직 구현
                current_type = volume_info.get('volume_type', '')
                target_type = self._determine_target_volume_type(current_type)

                logger.info(f"볼륨 {volume_id}의 타입 변경: {current_type} -> {target_type}")

                if current_type == target_type:
                    logger.info(f"볼륨 {volume_id}는 이미 최적의 타입({target_type})입니다.")
                    result['details']['message'] = f"볼륨 유형 {current_type}에서 변경이 필요하지 않습니다."
                    result['success'] = True
                    return result

                # 현재 볼륨 상태 확인
                volume_detail = self._get_volume_info(volume_id)
                if not volume_detail:
                    result['details']['error'] = "볼륨 정보를 가져올 수 없어 타입 변경을 진행할 수 없습니다."
                    return result

                logger.info(f"볼륨 {volume_id} 상세 정보 확인 완료")

                # 볼륨이 사용 중인지 확인
                if volume_detail.get('State') != 'available' and volume_detail.get('Attachments'):
                    logger.warning(f"볼륨 {volume_id}이(가) 인스턴스에 연결된 상태입니다. 연결된 상태에서도 타입 변경을 진행합니다.")

                # 볼륨 타입 변경 요청
                change_initiated = self._initiate_volume_type_change(volume_id, target_type, volume_info=volume_info)

                if not change_initiated:
                    result['details']['error'] = f"볼륨 타입을 {current_type}에서 {target_type}으로 변경 요청 실패"
                    return result

                result['details']['action'] = f"볼륨 타입을 {current_type}에서 {target_type}으로 변경 요청 완료"
                result['details']['note'] = "볼륨 타입 변경은 백그라운드에서 계속 진행됩니다. 완료까지 몇 분 소요될 수 있습니다."
                result['details']['previous_type'] = current_type
                result['details']['target_type'] = target_type
                result['success'] = True

            elif action_type == 'change_type_and_resize':
                # 볼륨 타입 변경 및 크기 조정 로직 구현
                current_type = volume_info.get('volume_type', '')
                current_size = volume_info.get('size', 0)
                target_type = self._determine_target_volume_type(current_type)

                # 유휴 볼륨의 경우 최소 크기로 축소
                target_size = max(1, current_size // 2)  # 최소 1GB, 또는 현재 크기의 절반

                modification_initiated = self._initiate_volume_modification(volume_id, target_type, target_size, volume_info=volume_info)
                if not modification_initiated:
                    result['details']['error'] = f"볼륨 속성 변경 요청 실패 (타입: {current_type}->{target_type}, 크기: {current_size}->{target_size}GB)"
                    return result

                result['details']['action'] = f"볼륨 속성 변경 요청 완료 (타입: {current_type}->{target_type}, 크기: {current_size}->{target_size}GB)"
                result['details']['note'] = "볼륨 속성 변경은 백그라운드에서 계속 진행됩니다. 완료까지 몇 분 소요될 수 있습니다."
                result['details']['previous_type'] = current_type
                result['details']['previous_size'] = current_size
                result['details']['target_type'] = target_type
                result['details']['target_size'] = target_size
                result['success'] = True

            else:
                result['details']['error'] = f"지원되지 않는 작업 유형: {action_type}"

        except Exception as e:
            logger.error(f"권장 조치 실행 중 오류 발생: {str(e)}", exc_info=True)
            result['details']['error'] = str(e)

        # 실행 기록 저장
        self.execution_history.append(result)

        return result

    def execute_overprovisioned_volume_recommendation(self, volume_info, action_type):
        """
        과대 프로비저닝된 볼륨에 대한 권장 조치를 실행합니다.
        비동기적으로 처리하여 슬랙 액션이 오래 기다리지 않도록 합니다.

        :param volume_info: 볼륨 정보 딕셔너리
        :param action_type: 실행할 조치 유형 ('resize', 'change_type', 'change_type_and_resize')
        :return: 결과 딕셔너리
        """
        volume_id = volume_info['volume_id']
        result = {
            'volume_id': volume_id,
            'action_type': action_type,
            'success': False,
            'timestamp': datetime.now().isoformat(),
            'details': {},
            'status': 'initiated' # 작업이 시작되었음을 표시
        }

        # 유효한 작업 유형 확인
        valid_overprovisioned_actions = ['resize', 'change_type', 'change_type_and_resize']
        if action_type not in valid_overprovisioned_actions:
            error_msg = f"과대 프로비저닝 볼륨에 지원되지 않는 작업 유형: {action_type}. 유효한 작업: {valid_overprovisioned_actions}"
            logger.error(error_msg)
            result['details']['error'] = error_msg
            return result

        # --- 루트 볼륨 보호 로직 시작 ---
        is_root = False
        if volume_info.get('attached_instances'):
            for attachment in volume_info['attached_instances']:
                instance_id = attachment.get('instance_id')
                device_name = attachment.get('device')
                if instance_id and device_name:
                    if self._is_root_volume(instance_id, device_name):
                        is_root = True
                        break

        # 루트 볼륨에 대해 크기 축소 방지
        if is_root and action_type in ['resize', 'change_type_and_resize']:
            # !!! 중요: _calculate_recommended_size는 아직 개선 전입니다. !!!
            # !!! 추후 개선된 로직이 반영되면 이 부분은 더 정확해집니다. !!!
            target_size = self._calculate_recommended_size(volume_info)
            current_size = volume_info.get('size', 0)

            # 루트 볼륨이면서 크기를 줄이려고 할 때
            if target_size < current_size:
                warning_msg = f"작업 건너뜀: 볼륨 {volume_id}은(는) 루트 볼륨이므로 크기 축소({current_size}GB -> {target_size}GB) 작업을 자동으로 실행할 수 없습니다."
                logger.warning(warning_msg)
                result['success'] = False
                result['status'] = 'skipped_root_volume_resize'
                result['details']['error'] = warning_msg
                self.execution_history.append(result)
                return result

        # --- 루트 볼륨 보호 로직 끝 ---

        logger.info(f"볼륨 {volume_id}에 대한 '{action_type}' 작업 시작 중...")

        try:
            # 액션 실행
            if action_type == 'resize':
                # 크기 조정
                target_size = self._calculate_recommended_size(volume_info)
                resize_initiated = self._initiate_resize_volume(volume_id, target_size, volume_info=volume_info)

                if not resize_initiated:
                    result['details']['error'] = f"볼륨 크기 조정 요청 실패 ({volume_info.get('size', 0)} -> {target_size}GB)"
                    return result

                result['details']['action'] = f"볼륨 크기 조정 요청 완료 ({volume_info.get('size', 0)} -> {target_size}GB)"
                result['details']['note'] = "볼륨 크기 조정은 백그라운드에서 계속 진행됩니다. 완료까지 몇 분 소요될 수 있습니다."
                result['details']['previous_size'] = volume_info.get('size', 0)
                result['details']['target_size'] = target_size
                result['success'] = True

            elif action_type == 'change_type':
                # 타입 변경
                current_type = volume_info.get('volume_type', '')
                target_type = self._determine_target_volume_type(current_type)

                if current_type == target_type:
                    result['details']['message'] = f"볼륨 유형 {current_type}에서 변경이 필요하지 않습니다."
                    result['success'] = True
                    return result

                change_initiated = self._initiate_volume_type_change(volume_id, target_type, volume_info=volume_info)

                if not change_initiated:
                    result['details']['error'] = f"볼륨 타입을 {current_type}에서 {target_type}으로 변경 요청 실패"
                    return result

                result['details']['action'] = f"볼륨 타입을 {current_type}에서 {target_type}으로 변경 요청 완료"
                result['details']['note'] = "볼륨 타입 변경은 백그라운드에서 계속 진행됩니다. 완료까지 몇 분 소요될 수 있습니다."
                result['details']['previous_type'] = current_type
                result['details']['target_type'] = target_type
                result['success'] = True

            elif action_type == 'change_type_and_resize':
                # 타입 변경 및 크기 조정
                current_type = volume_info.get('volume_type', '')
                current_size = volume_info.get('size', 0)
                target_type = self._determine_target_volume_type(current_type)
                target_size = self._calculate_recommended_size(volume_info)

                modification_initiated = self._initiate_volume_modification(volume_id, target_type, target_size, volume_info=volume_info)
                if not modification_initiated:
                    result['details']['error'] = f"볼륨 속성 변경 요청 실패 (타입: {current_type}->{target_type}, 크기: {current_size}->{target_size}GB)"
                    return result

                result['details']['action'] = f"볼륨 속성 변경 요청 완료 (타입: {current_type}->{target_type}, 크기: {current_size}->{target_size}GB)"
                result['details']['note'] = "볼륨 속성 변경은 백그라운드에서 계속 진행됩니다. 완료까지 몇 분 소요될 수 있습니다."
                result['details']['previous_type'] = current_type
                result['details']['previous_size'] = current_size
                result['details']['target_type'] = target_type
                result['details']['target_size'] = target_size
                result['success'] = True

            else:
                result['details']['error'] = f"지원되지 않는 작업 유형: {action_type}"

        except Exception as e:
            logger.error(f"과대 프로비저닝 권장 조치 실행 중 오류 발생: {str(e)}", exc_info=True)
            result['details']['error'] = str(e)

        # 실행 기록 저장
        self.execution_history.append(result)

        return result

    def _initiate_volume_type_change(self, volume_id, target_type, volume_info=None):
        """
        볼륨 타입 변경 요청 시작
        """
        try:
            # 필요한 정보가 없으면 현재 볼륨 정보 가져오기
            if not volume_info:
                volume_info = self._get_volume_info(volume_id)
                if not volume_info:
                     logger.error(f"볼륨 {volume_id} 정보를 가져올 수 없어 타입 변경을 시작할 수 없습니다.")
                     return False

            # 변경 요청
            iops = volume_info.get('iops')
            throughput = volume_info.get('throughput')

            change_result = self.ebs_action_executor.modify_volume_type(volume_id, target_type, iops=iops, throughput=throughput)

            if change_result['success']:
                logger.info(f"볼륨 {volume_id}의 타입 변경 요청 시작됨. 새 타입: {target_type}")
                return True
            else:
                logger.error(f"볼륨 {volume_id}의 타입 변경 요청 실패: {change_result.get('error')}")
                return False
        except Exception as e:
            logger.error(f"볼륨 {volume_id} 타입 변경 시작 중 예외 발생: {str(e)}", exc_info=True)
            return False

    def _initiate_resize_volume(self, volume_id, target_size, volume_info=None):
        """
        볼륨 크기 조정 요청 시작
        """
        try:
            # 현재 볼륨 정보 (축소 방지 위해)
            if not volume_info:
                 volume_info = self._get_volume_info(volume_id)
                 if not volume_info:
                     logger.error(f"볼륨 {volume_id} 정보를 가져올 수 없어 크기 조정을 시작할 수 없습니다.")
                     return False

            current_size = volume_info.get('size', 0)

            if target_size < current_size:
                logger.warning(f"볼륨 {volume_id}의 크기 축소 ({current_size} -> {target_size}GB)는 지원되지 않아 건너뜁니다.")
                return False # 축소는 지원 안 함

            resize_result = self.ebs_action_executor.modify_volume_size(volume_id, target_size)

            if resize_result['success']:
                logger.info(f"볼륨 {volume_id}의 크기 조정 요청 시작됨. 새 크기: {target_size}GB")
                return True
            else:
                logger.error(f"볼륨 {volume_id}의 크기 조정 요청 실패: {resize_result.get('error')}")
                return False
        except Exception as e:
            logger.error(f"볼륨 {volume_id} 크기 조정 시작 중 예외 발생: {str(e)}", exc_info=True)
            return False

    def _initiate_volume_modification(self, volume_id, target_type, target_size, volume_info=None):
        """
        볼륨 타입과 크기 동시 변경 요청 시작
        """
        try:
            # 현재 볼륨 정보 가져오기
            if not volume_info:
                volume_info = self._get_volume_info(volume_id)
                if not volume_info:
                     logger.error(f"볼륨 {volume_id} 정보를 가져올 수 없어 변경을 시작할 수 없습니다.")
                     return False

            current_type = volume_info.get('volume_type', '')
            current_size = volume_info.get('size', 0)
            current_iops = volume_info.get('iops')
            current_throughput = volume_info.get('throughput')

            # 타입과 크기가 모두 변경되지 않은 경우
            if current_type == target_type and current_size == target_size:
                logger.info(f"볼륨 {volume_id}은(는) 이미 요청된 타입({target_type})과 크기({target_size}GB)입니다.")
                return True # 이미 목표 상태

            # 크기 축소 방지
            if target_size < current_size:
                logger.warning(f"볼륨 {volume_id}의 크기 축소({current_size}GB -> {target_size}GB)는 지원되지 않습니다.")
                return False

            # 변경 요청 준비
            modify_args = {
                'VolumeId': volume_id
            }

            # 타입이 변경되면 추가
            if current_type != target_type:
                modify_args['VolumeType'] = target_type

                # 볼륨 타입에 따른 추가 파라미터 설정
                if target_type in ['io1', 'io2']:
                    modify_args['Iops'] = current_iops if current_iops is not None else 100
                elif target_type == 'gp3':
                    modify_args['Iops'] = current_iops if current_iops is not None else 3000
                    modify_args['Throughput'] = current_throughput if current_throughput is not None else 125

            # 크기가 변경되면 추가
            if current_size != target_size:
                modify_args['Size'] = target_size

            # 변경 요청 실행
            response = self.ec2_client.modify_volume(**modify_args)

            # 변경 상태 확인
            modification = response.get('VolumeModification', {})
            start_state = modification.get('ModificationState')

            logger.info(f"볼륨 {volume_id} 속성 변경 요청 완료. 변경은 백그라운드에서 계속됩니다.")

            return True

        except Exception as e:
            logger.error(f"볼륨 {volume_id} 속성 변경 시작 중 예외 발생: {str(e)}", exc_info=True)
            return False

    def get_execution_history(self):
        """
        실행 기록 반환
        """
        return self.execution_history

    def save_execution_history(self, filepath):
        """
        실행 기록을 JSON 파일로 저장
        """
        try:
            with open(filepath, 'w') as f:
                json.dump(self.execution_history, f, indent=4)
            logger.info(f"실행 기록이 {filepath}에 저장되었습니다.")
        except Exception as e:
            logger.error(f"실행 기록 저장 중 오류 발생: {str(e)}", exc_info=True)

    def _get_volume_info(self, volume_id):
        """
        단일 볼륨 정보 조회
        """
        try:
            response = self.ec2_client.describe_volumes(VolumeIds=[volume_id])
            if response['Volumes']:
                volume = response['Volumes'][0]
                # 추가 정보 추출 (타입, 크기 등)
                volume_info = {
                    'volume_id': volume['VolumeId'],
                    'volume_type': volume['VolumeType'],
                    'size': volume['Size'],
                    'iops': volume.get('Iops'),
                    'throughput': volume.get('Throughput'),
                    'state': volume.get('State'),
                    'attachments': volume.get('Attachments', [])
                }
                return volume_info
        except Exception as e:
            logger.error(f"볼륨 {volume_id} 정보 조회 중 오류: {str(e)}")
        return None

    def _create_snapshot(self, volume_id, description, tags):
        """
        스냅샷 생성을 시작하고 스냅샷 ID 반환
        """
        try:
            # 태그 변환
            tag_specs = [{
                'ResourceType': 'snapshot',
                'Tags': [{'Key': k, 'Value': v} for k, v in tags.items()]
            }]
            
            response = self.ec2_client.create_snapshot(
                VolumeId=volume_id,
                Description=description,
                TagSpecifications=tag_specs
            )
            snapshot_id = response.get('SnapshotId')
            logger.info(f"스냅샷 {snapshot_id} 생성 시작됨.")
            return snapshot_id
        except Exception as e:
            logger.error(f"스냅샷 생성 중 오류: {str(e)}")
            return None

    def check_snapshot_status(self, snapshot_id):
        """
        스냅샷 상태 확인
        """
        try:
            response = self.ec2_client.describe_snapshots(SnapshotIds=[snapshot_id])
            if response['Snapshots']:
                state = response['Snapshots'][0]['State']
                logger.info(f"스냅샷 {snapshot_id} 상태: {state}")
                return state
            else:
                logger.warning(f"스냅샷 {snapshot_id}을 찾을 수 없습니다.")
                return None
        except Exception as e:
            logger.error(f"스냅샷 {snapshot_id} 상태 확인 중 오류: {str(e)}")
            return None

    def check_volume_status(self, volume_id):
        """
        볼륨 상태 확인
        """
        try:
            response = self.ec2_client.describe_volumes(VolumeIds=[volume_id])
            if response['Volumes']:
                state = response['Volumes'][0]['State']
                logger.info(f"볼륨 {volume_id} 상태: {state}")
                return state
            else:
                # 볼륨이 없는 경우 (삭제되었을 수 있음)
                logger.warning(f"볼륨 {volume_id}을 찾을 수 없습니다.")
                return 'not_found'
        except Exception as e:
            logger.error(f"볼륨 {volume_id} 상태 확인 중 오류: {str(e)}")
            return 'error'

    def check_volume_modification_status(self, volume_id):
        """
        볼륨 변경 작업 상태 확인
        """
        try:
            response = self.ec2_client.describe_volumes_modifications(VolumeIds=[volume_id])
            modifications = response.get('VolumesModifications', [])
            
            if not modifications:
                logger.info(f"볼륨 {volume_id}에 대한 진행 중인 변경 작업이 없습니다.")
                return 'no_modification'
                
            # 가장 최근 변경 작업 상태 반환
            latest_modification = sorted(modifications, key=lambda x: x['StartTime'], reverse=True)[0]
            state = latest_modification.get('ModificationState')
            logger.info(f"볼륨 {volume_id} 변경 상태: {state}")
            return state
            
        except Exception as e:
            logger.error(f"볼륨 {volume_id} 변경 상태 확인 중 오류: {str(e)}")
            return 'error'

    def _is_root_volume(self, instance_id, device_name):
        """
        주어진 디바이스가 인스턴스의 루트 볼륨인지 확인
        """
        try:
            response = self.ec2_client.describe_instances(InstanceIds=[instance_id])
            if not response['Reservations'] or not response['Reservations'][0]['Instances']:
                logger.warning(f"루트 볼륨 확인 중 인스턴스 {instance_id}를 찾을 수 없습니다.")
                return False # 인스턴스 없으면 루트 볼륨 아님
            
            instance = response['Reservations'][0]['Instances'][0]
            root_device = instance.get('RootDeviceName')
            
            if root_device and root_device == device_name:
                return True
            return False
            
        except Exception as e:
            logger.error(f"루트 볼륨 확인 중 오류 발생 (인스턴스: {instance_id}, 디바이스: {device_name}): {str(e)}")
            return False # 오류 발생 시 안전하게 루트가 아니라고 가정

    def _determine_target_volume_type(self, current_type):
        """
        현재 볼륨 타입에 따라 변경할 목표 타입 결정 (단순 예시)
        """
        # 과대 프로비저닝 분석 결과 등을 반영하여 더 정교하게 결정 필요
        if current_type in ['io1', 'io2', 'gp2', 'st1', 'sc1', 'standard']:
            return 'gp3' # 대부분의 경우 gp3가 비용 효율적
        else:
            return current_type # gp3는 그대로 유지

    def _calculate_recommended_size(self, volume_info):
        """
        권장 볼륨 크기 계산 (단순 예시)
        !!! 중요: 이 함수는 Placeholder이며, 실제 구현 시
        overprovisioned_detector의 분석 결과를 사용해야 합니다. !!!
        
        :param volume_info: 볼륨 정보
        :return: 권장 크기 (GB)
        """
        # Placeholder: 현재 크기의 80% 또는 최소 1GB (실제 로직으로 교체 필요)
        current_size = volume_info.get('size', 1) # 기본값 1GB
        recommended_size = max(1, int(current_size * 0.8))
        
        logger.warning(f"임시 권장 크기 계산: 현재 {current_size}GB -> 권장 {recommended_size}GB. 추후 분석 로직으로 대체 필요!")
        
        return recommended_size 