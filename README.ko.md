[English](README.md) · **한국어** · [中文](README.zh.md)

# Synapse Novel

Synapse Novel은 소설을 비트 단위로 계획하고 검사하는 공개 작성판입니다. 사람은 이미 사용하는 AI와 함께 씁니다. Synapse는 매번 작성 요청을 만들고, 돌아온 글을 확인된 정본 및 계획된 행위자와 대조하고, 프로젝트 상태를 기록합니다. Synapse는 모델을 호출하지 않습니다. 로컬 모델 작성은 이 판에 포함되지 않습니다.

## 시작 안내

[연결 안내서](CONNECT.ko.md)에서 Linux와 Windows의 MCP 설정을 확인하세요. CLI는 [판본 안내서](editions/novel/README.ko.md)에 설명되어 있습니다. Python 3.12와 PyYAML이 필요합니다.

소설 CLI 명령은 `import`, `audit`, `seed`, `plan`, `plan-confirm`, `beat`, `check`, `lock`, `status`, `assemble`입니다. `python editions/novel/synapse_novel.py novel --help`에서 전체 목록을 볼 수 있습니다.

### 이미 쓴 원고

장별 `.txt`와 `.md` 파일을 한 폴더에 바로 넣고 `novel import --project P --manuscript DIR`를 실행하세요. `synapse_value_propose`로 정본 값을 제안하고 내용을 살핀 뒤, 터미널에서 `canon values confirm`으로 확인하세요. 그런 다음 `novel audit --project P`를 실행합니다. 가져오기는 프로젝트 안에 스냅샷을 저장하며 원본 폴더는 바꾸지 않습니다. 다시 가져오려면 `--replace`를 사용하세요.

## 소설 도구 일곱 개

로컬 MCP 서버는 다음 도구를 먼저 나열합니다: `synapse_novel_seed`, `synapse_novel_plan`, `synapse_novel_beat`, `synapse_novel_check`, `synapse_novel_status`, `synapse_novel_import`, `synapse_novel_audit`. 원고 가져오기와 감사 도구는 정본 값을 확인하거나 무엇이든 잠그지 않습니다. 계획 확인, 정본 값 확인, 비트 잠금, 장면 조립은 터미널 CLI에서만 할 수 있습니다. 서버와 CLI는 모델이나 네트워크를 호출하지 않습니다.

프리셋과 정본 관련 도구도 [연결 안내서](CONNECT.ko.md)에 있습니다. 이 공개 판에는 `novel_upload_review`와 `novel_philosophy_depth` 두 프리셋 정의가 포함됩니다.

## 결과 필드 읽기

사람에게 보여 주는 메시지와 오류 문구는 한국어입니다. JSON 키는 바꾸지 않는 계약 필드입니다. 키 이름을 번역하거나 바꾸지 말고 해당 값과 함께 읽으세요.

- `not_examined`는 원문 `미검토`를 뜻합니다. 잠금을 막으며, 필요한 원문을 살펴본 뒤 비트를 다시 검사해야 합니다.
- `unchecked`는 `미확인` 정본 값을 뜻합니다. 잠금을 막지는 않지만, 터미널 잠금 단계에서 보여 주고 사람이 직접 쓴 말을 기록합니다.
- 확정 충돌, 거짓인 `facts_match`, 계획된 인물 누락, 현재 계획 미확인은 잠금을 막습니다. 가능한 충돌과 품질 조언은 막지 않습니다. 품질은 문학적 판정이 아니라 조언입니다.

계획 기록에는 프로젝트의 씨앗 파일과 정본 값 파일에서 만든 입력 지문이 들어가고, 계획 해시는 그 지문을 포함합니다. 씨앗이나 값이 달라지면 계획을 다시 만들고 새 해시를 터미널에서 확인해야 합니다.

## 합성 예제

예제는 등대를 다룬 합성 이야기에서 가져온 장면 `ch1s1` 하나와 세 비트로 이루어져 있습니다. 씨앗, 계획, 예시 글은 모두 합성 자료입니다. [전체 예제 안내](editions/novel/examples/novel/README.ko.md)에는 제안된 씨앗 키를 하나씩 확인하고 비트 1–3을 요청·검사·잠근 뒤 상태를 읽고 장면을 조립하는 순서가 있습니다. 씨앗 때 저장한 프로젝트 정체는 폴더를 옮겨도 유지됩니다.

잠긴 비트의 글, 확인된 계획 또는 그 씨앗·값 입력, 정본 블록, 같은 장면의 앞선 비트가 달라지면 잠금이 stale 상태가 됩니다. 순서대로 복구하세요. 먼저 `status`를 읽고, 입력이 바뀌었다면 계획을 고쳐 다시 만든 뒤 새 해시를 확인합니다. 가장 앞선 stale 비트부터 시작합니다. 계획·정본 맥락·앞선 비트가 바뀌었다면 다시 요청하고, 새 글을 검사한 뒤 터미널에서 잠급니다. 같은 장면의 뒤 비트도 차례대로 각각 요청·검사·잠급니다. 마지막으로 status를 다시 읽어 stale 또는 미잠금 비트가 없을 때만 조립합니다. 조립은 기존 파일을 덮어쓰지 않으므로 새 출력 경로를 사용하세요.

MCP 서버는 `SYNAPSE_NOVEL_ROOT`로 등록한 기존 폴더 안의 경로만 읽고 씁니다. 씨앗을 만들 때 요청한 프로젝트 폴더는 이 안에 생성됩니다. 씨앗, 계획, 비트 입력도 모두 그 폴더 아래에 두세요.

## 프로젝트 참고 자료

Synapse 전체 프로젝트에서 이어받은 배경 참고 자료는 [팟캐스트 회차 목록](https://podcasts.apple.com/us/podcast/our-field-trip-to-google-i-o-a-sit-down-with/id1528594034?i=1000769070660)과 [Hard Fork 공개 transcript](https://bidclub.ai/zh/e/hard-fork-live-part-2-dylan-field-standing-out-i)입니다.

## 지원 및 라이선스

개인정보를 지운 신고는 [FEEDBACK.md](FEEDBACK.md)로 보내고, 처리 순서는 [FEEDBACK_TRIAGE.md](FEEDBACK_TRIAGE.md)를 확인하세요. 이용 조건은 [라이선스](LICENSE)에 있습니다. 이 저장소의 릴리스 기록은 [RELEASES.md](RELEASES.md)에 있습니다.
