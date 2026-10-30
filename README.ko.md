[English](README.md) · **한국어** · [中文](README.zh.md)

# Synapse Novel

Synapse Novel은 소설을 비트 단위로 계획하고 검사하는 공개 작성판입니다. 사람은 이미 사용하는 AI와 함께 씁니다. Synapse는 매번 작성 요청을 만들고, 돌아온 글을 확인된 정본 및 계획된 행위자와 대조하고, 프로젝트 상태를 기록합니다. Synapse는 모델을 호출하지 않습니다. 로컬 모델 작성은 이 판에 포함되지 않습니다.

## 시작 안내

[연결 안내서](CONNECT.ko.md)에서 Linux와 Windows의 MCP 설정을 확인하세요. CLI는 [판본 안내서](editions/novel/README.ko.md)에 설명되어 있습니다. Python 3.12와 PyYAML이 필요합니다.

소설 CLI 명령은 열 개입니다: `import`, `audit`, `seed`, `plan`, `plan-confirm`, `beat`, `check`, `lock`, `status`, `assemble`. `python editions/novel/synapse_novel.py novel --help`에서 전체 목록을 볼 수 있습니다.

### 이미 쓴 원고

장별 `.txt`, `.md`, `.docx` 파일을 한 폴더에 바로 넣고 `novel import --project P --manuscript DIR`를 실행하세요. 장마다 DOCX 파일을 둘 수도 있고, 단일 `.docx`의 Word `제목 1` (`heading 1`) 문단마다 장을 나눌 수도 있습니다. 가져오기가 붙이는 장 번호는 자연스러운 파일명 정렬 순서입니다. 파일명 속 숫자가 아니라 가져온 순서이므로 `10.md`가 4장이 될 수도 있습니다. 가져오기는 프로젝트 안에 스냅샷을 저장하며 원본 폴더는 바꾸지 않습니다. 다시 가져오려면 `--replace`를 사용하세요.

워드 파일은 변경 추적을 모두 받아들인 뒤의 글만 검사하고, 위치는 문단 번호로 알려 줍니다(`line_unit: "paragraph"`, 빈 문단까지 파일 전체를 셉니다. 텍스트 파일은 `line_unit: "line"`). 폴더에 장 파일이 `.docx` 하나뿐이면 첫 `제목 1`(스타일 이름으로 찾으므로 영어 워드의 `heading 1`도 됩니다) 앞의 문단은 장이 아니라 `front_matter`로 따로 적고, 각 제목은 그 장의 `title`이 됩니다. 다른 장 파일과 함께 있으면 `.docx`는 통째로 한 장으로 읽고, 그렇게 했다는 것을 `warnings`에 적습니다. 옛 워드 `.doc`/`.docm`과 한글 `.hwp`/`.hwpx`는 읽지 않습니다. 폴더에 하나라도 있으면 가져오기가 멈추니 먼저 `.docx`로 저장하거나 `.txt`로 내보내세요. `novel status`는 바뀌거나 없어진 장을 `manuscript.stale`, 새 장을 `manuscript.added`, 읽을 수 없는 파일을 `manuscript.unsupported`에 보여 줍니다.

AI 없이 시작하려면 [씨앗 카드 예시](editions/novel/examples/novel/seed.json)를 직접 채우고 `python editions/novel/synapse_novel.py novel seed --project P --seed-file F`를 실행하세요. 원고를 가져오기 전이나, 가져온 뒤 정본 값 파일이 비어 있을 때 실행할 수 있습니다. 먼저 씨앗을 만들면 가져오기가 그 프로젝트 파일을 보존하고, 먼저 원고를 가져오면 씨앗 명령이 빈 정본 값 파일을 채웁니다. 씨앗 결과에는 제안된 값마다 터미널에서 실행할 전체 확인 명령이 나옵니다. 각 명령을 살펴보고 직접 실행한 다음 `novel audit --project P`를 실행하세요.

주장에서 AI가 정본 값을 제안하게 하려면 MCP 도구 `synapse_value_propose`를 사용합니다. 이 경로도 제안만 하며, 사람은 각 값을 살펴본 뒤 터미널에서 `canon values confirm`으로 확인해야 합니다.

## 소설 도구 일곱 개

로컬 MCP 서버는 다음 도구를 먼저 나열합니다: `synapse_novel_seed`, `synapse_novel_plan`, `synapse_novel_beat`, `synapse_novel_check`, `synapse_novel_status`, `synapse_novel_import`, `synapse_novel_audit`. 원고 가져오기와 감사 도구는 정본 값을 확인하거나 무엇이든 잠그지 않습니다. 계획 확인, 정본 값 확인, 비트 잠금, 장면 조립은 터미널 CLI에서만 할 수 있습니다. 서버와 CLI는 모델이나 네트워크를 호출하지 않습니다.

프리셋과 정본 관련 도구도 [연결 안내서](CONNECT.ko.md)에 있습니다. 이 공개 판에는 `novel_upload_review`와 `novel_philosophy_depth` 두 프리셋 정의가 포함됩니다.

## 결과 필드 읽기

사람에게 보여 주는 메시지와 오류 문구는 한국어입니다. JSON 키는 바꾸지 않는 계약 필드입니다. 키 이름을 번역하거나 바꾸지 말고 해당 값과 함께 읽으세요.

- `not_examined`는 원문 `미검토`를 뜻합니다. 잠금을 막으며, 필요한 원문을 살펴본 뒤 비트를 다시 검사해야 합니다.
- `unchecked`는 원고 문장과 대조하지 않는 정본 값입니다. `requirement` 값은 정책상 텍스트 검사 대상이 아니므로 `complete`를 계속 false로 두며, 글에 충돌이 없어도 `canon values check`가 종료 코드 `1`로 끝날 수 있습니다. 이는 깨끗한 글에서 충돌을 찾았다는 뜻이 아니라 검사가 미완료라는 뜻입니다. 미확인 값은 비트 잠금을 막지 않지만 터미널 잠금 단계에서 보여 주고 사람이 직접 쓴 말을 기록합니다.
- 확정 충돌, 거짓인 `facts_match`, 계획된 인물 누락, 현재 계획 미확인은 잠금을 막습니다. 가능한 충돌과 품질 조언은 막지 않습니다. 품질은 문학적 판정이 아니라 조언입니다.

계획 기록에는 프로젝트의 씨앗 파일과 정본 값 파일에서 만든 입력 지문이 들어가고, 계획 해시는 그 지문을 포함합니다. 씨앗이나 값이 달라지면 계획을 다시 만들고 새 해시를 터미널에서 확인해야 합니다.

`status`의 최상위 `stale`은 비트 상태이고, `manuscript.stale`은 원본이 바뀌거나 사라진 가져온 장 파일 목록입니다.

## 합성 예제

예제는 등대를 다룬 합성 이야기에서 가져온 장면 `ch1s1` 하나와 세 비트로 이루어져 있습니다. 씨앗, 계획, 예시 글은 모두 합성 자료입니다. [전체 예제 안내](editions/novel/examples/novel/README.ko.md)에는 제안된 씨앗 키를 하나씩 확인하고 비트 1–3을 요청·검사·잠근 뒤 상태를 읽고 장면을 조립하는 순서가 있습니다. 씨앗 때 저장한 프로젝트 정체는 폴더를 옮겨도 유지됩니다.

잠긴 비트의 글, 확인된 계획 또는 그 씨앗·값 입력, 정본 블록, 같은 장면의 앞선 비트가 달라지면 잠금이 stale 상태가 됩니다. 순서대로 복구하세요. 먼저 `status`를 읽고, 입력이 바뀌었다면 계획을 고쳐 다시 만든 뒤 새 해시를 확인합니다. 가장 앞선 stale 비트부터 시작합니다. 계획·정본 맥락·앞선 비트가 바뀌었다면 다시 요청하고, 새 글을 검사한 뒤 터미널에서 잠급니다. 같은 장면의 뒤 비트도 차례대로 각각 요청·검사·잠급니다. 마지막으로 status를 다시 읽어 stale 또는 미잠금 비트가 없을 때만 조립합니다. 조립은 기존 파일을 덮어쓰지 않으므로 새 출력 경로를 사용하세요.

MCP 서버는 `SYNAPSE_NOVEL_ROOT`로 등록한 기존 폴더 안의 경로만 읽고 씁니다. 씨앗을 만들 때 요청한 프로젝트 폴더는 이 안에 생성됩니다. 씨앗, 계획, 비트 입력도 모두 그 폴더 아래에 두세요.

## 프로젝트 참고 자료

Synapse 전체 프로젝트에서 이어받은 배경 참고 자료는 [팟캐스트 회차 목록](https://podcasts.apple.com/us/podcast/our-field-trip-to-google-i-o-a-sit-down-with/id1528594034?i=1000769070660)과 [Hard Fork 공개 transcript](https://bidclub.ai/zh/e/hard-fork-live-part-2-dylan-field-standing-out-i)입니다.

## 지원 및 라이선스

개인정보를 지운 신고는 [FEEDBACK.md](FEEDBACK.md)로 보내고, 처리 순서는 [FEEDBACK_TRIAGE.md](FEEDBACK_TRIAGE.md)를 확인하세요. 이용 조건은 [라이선스](LICENSE)에 있습니다. 이 저장소의 릴리스 기록은 [RELEASES.md](RELEASES.md)에 있습니다.
