[English](README.md) · **한국어** · [中文](README.zh.md)

# Synapse Novel 판본

이 폴더에는 공개 소설 작성 흐름이 있습니다. 비트별 작성 요청을 준비하고 확인된 정본과 계획된 행위자를 기준으로 글을 검사하며 상태를 기록합니다. 글은 사람이 사용하는 AI가 씁니다. Synapse는 모델을 호출하지 않습니다. 로컬 모델 작성은 이 판에 포함되지 않습니다.

## CLI 명령

저장소 루트에서 Python 3.12와 PyYAML로 실행합니다. `P`는 이미 있는 프로젝트 폴더이고 `F`, `T`, `O`는 사용자가 정하는 경로입니다.

| 명령 | 기능 |
|---|---|
| `novel seed --project P --seed-file F` | 씨앗 기록을 만들고 정본 값을 제안합니다. 기존 씨앗 출력은 덮어쓰지 않습니다. |
| `canon values confirm P/novel/canon-values.json KEY --files P/novel` | 터미널에서 한 번에 하나씩 사람이 자기 말로 제안 값을 확인합니다. |
| `novel plan --project P --plan-file F` | 장면 계획을 검사합니다. 모든 비트에 `"confirmed": true`가 있고 행위자는 장면 인물이어야 합니다. |
| `novel plan-confirm --project P` | 터미널에서 저장된 계획 해시를 확인합니다. |
| `novel beat --project P --scene S --beat N` | 다음 순서의 비트에 공용 작성 요청과 확인 정본 블록을 기록합니다. 모델은 호출하지 않습니다. |
| `novel check --project P --scene S --beat N --text-file T` | 정본과 계획 행위자를 기준으로 글을 검사하고 비트와 영수증을 저장합니다. |
| `novel lock --project P --scene S --beat N` | 다시 검사하고 터미널에서 사람이 직접 입력한 말과 함께 비트를 잠급니다. |
| `novel status --project P` | 계획과 비트별 요청·검사·잠금·stale 상태를 읽습니다. |
| `novel assemble --project P --scene S --out O` | 모든 비트가 최신이고 잠겼을 때 장면을 순서대로 조립합니다. 출력 경로는 새 경로여야 합니다. |

`python editions/novel/synapse_novel.py novel --help`에서 여덟 흐름 명령을 확인하세요. 종료 코드 `0`은 완료, `1`은 아직 해결할 항목이 있음, `2`는 실행할 수 없음을 뜻합니다. 사람이 읽는 메시지와 오류는 한국어입니다. JSON 필드 이름은 고정 계약 키이므로 바꾸지 않습니다.

`not_examined`는 잠금을 막는 원문 `미검토` 상태입니다. 필요한 원문을 살펴보고 비트를 다시 검사해야 합니다. `unchecked` 정본 값은 `미확인` 상태로 잠금을 막지는 않으며, 터미널에서 잠글 때 보여 주고 사람의 말과 함께 기록합니다. 확정 충돌, 계획 인물 누락, 정본 맥락 불일치, 현재 계획 미확인은 잠금을 막습니다. 가능한 충돌과 품질 조언은 막지 않습니다. 품질은 조언이며 승인 판정이 아닙니다.

계획 입력 지문은 저장된 씨앗, 정본 값 파일, 정본 항목을 포함합니다. 계획 해시는 이 지문을 포함합니다. 씨앗이나 값이 바뀌면 계획을 다시 만들고 확인해야 합니다.

## 합성 예제

[등대 이야기 세 비트 전체 안내](examples/novel/README.ko.md)를 따라 하세요. 씨앗, 계획, 세 비트 글은 모두 합성 입력입니다. 안내서에는 제안된 씨앗 키 전부를 확인하고 비트 1–3을 순서대로 요청·검사한 뒤 터미널에서 각각 잠그고, 상태를 읽어 새 파일로 조립하는 과정이 있습니다.

잠긴 비트의 글, 확인 정본 블록, 계획 해시, 같은 장면의 앞선 비트가 달라지면 잠금이 stale 상태가 됩니다. 먼저 status를 읽습니다. 씨앗이나 정본 값이 바뀌었다면 계획을 다시 만들고 확인합니다. 가장 앞선 stale 비트부터 시작합니다. 계획·정본 맥락 또는 앞선 비트가 바뀌었으면 다시 요청하고 검사·잠급니다. 뒤 비트도 장면 순서대로 처리한 뒤 status를 다시 확인합니다. 모든 비트가 최신이고 잠긴 경우에만 조립합니다.

MCP 서버는 기존 `SYNAPSE_NOVEL_ROOT` 폴더 아래의 파일만 읽고 씁니다. 씨앗, 계획, 글 입력과 프로젝트를 이 폴더 안에 두세요.

## 공개 안내

- [공개 개요](../../README.ko.md)
- [AI 연결하기](../../CONNECT.ko.md)
- [작성 AI용 스킬](../../skills/synapse-novel/SKILL.md)

[한국어 합성 예제](examples/novel/README.ko.md), [English edition guide](README.md), [中文版本指南](README.zh.md)를 볼 수 있습니다.
