[English](README.md) · **한국어** · [中文](README.zh.md)

# 합성 등대 예제

이 예제는 합성 자료입니다. 《소리섬 등대의 마지막 겨울》 씨앗 카드, 장면 `ch1s1`의 확인된 세 비트 계획, 비트별 예시 글 파일이 들어 있습니다. 영수증이나 잠금 기록은 동봉하지 않습니다. 흐름은 만든 프로젝트 안에 이를 기록하며 모델을 호출하지 않습니다. 예시 글은 이미 작성되어 있으므로 그대로 검사합니다.

저장소 루트에서 Python 3.12와 PyYAML로 실행합니다. Windows에서는 `.venv\Scripts\python.exe`, Linux에서는 `.venv/bin/python`을 사용하세요.

## 1. 프로젝트를 만들고 씨앗 입력

~~~sh
mkdir -p .work/lighthouse
python editions/novel/synapse_novel.py novel seed --project .work/lighthouse --seed-file editions/novel/examples/novel/seed.json
~~~

`seed` 결과의 `proposed` 목록에는 다음 키만 들어갑니다.

- `work.title`
- `work.premise`
- `work.genre`
- `work.tone`
- `work.goal`
- `work.opposition`
- `work.timeframe`
- `work.ending_direction`
- `character.한도윤`, `character.한도윤.age`, `character.한도윤.sex`
- `character.서이나`, `character.서이나.age`, `character.서이나.sex`
- `character.오민규`, `character.오민규.age`, `character.오민규.sex`
- `rule.prohibition.0001`
- `rule.requirement.0001`

각 키를 하나씩 확인합니다. 아래 명령을 키마다 한 번 실행하고 `<KEY>`를 바꾸세요. 터미널이 물으면 자기 말을 입력합니다.

~~~text
python editions/novel/synapse_novel.py canon values confirm .work/lighthouse/novel/canon-values.json <KEY> --files .work/lighthouse/novel
~~~

예제가 대신 값을 확인하지 않습니다.

## 2. 장면 계획 저장 및 확인

동봉된 `plan.json`에는 `ch1s1` 장면과 세 비트가 있습니다. 모든 비트에 `"confirmed": true`와 `actors` 목록이 있습니다. 값을 유지하고 순서를 바꾸지 마세요. 비트 번호는 1부터 시작합니다.

~~~sh
python editions/novel/synapse_novel.py novel plan --project .work/lighthouse --plan-file editions/novel/examples/novel/plan.json
python editions/novel/synapse_novel.py novel plan-confirm --project .work/lighthouse
~~~

두 번째 명령에서는 사람이 터미널에서 자기 말을 입력합니다. 계획 해시는 이 프로젝트의 저장된 `seed.json`, `canon-values.json`, 정본 항목을 입력 지문으로 포함합니다. 씨앗이나 정본 값이 바뀌면 비트를 요청하기 전에 `plan`을 다시 실행하고 새 해시를 확인합니다.

## 3. 비트별 요청, 검사, 잠금

예시 글은 `editions/novel/examples/novel/beats/` 아래에 있습니다. 비트를 하나 요청하고, 해당 글 파일을 검사하고, 터미널에서 잠근 뒤 다음 비트를 요청하세요.

~~~sh
python editions/novel/synapse_novel.py novel beat --project .work/lighthouse --scene ch1s1 --beat 1
python editions/novel/synapse_novel.py novel check --project .work/lighthouse --scene ch1s1 --beat 1 --text-file editions/novel/examples/novel/beats/ch1s1-1.txt
python editions/novel/synapse_novel.py novel lock --project .work/lighthouse --scene ch1s1 --beat 1

python editions/novel/synapse_novel.py novel beat --project .work/lighthouse --scene ch1s1 --beat 2
python editions/novel/synapse_novel.py novel check --project .work/lighthouse --scene ch1s1 --beat 2 --text-file editions/novel/examples/novel/beats/ch1s1-2.txt
python editions/novel/synapse_novel.py novel lock --project .work/lighthouse --scene ch1s1 --beat 2

python editions/novel/synapse_novel.py novel beat --project .work/lighthouse --scene ch1s1 --beat 3
python editions/novel/synapse_novel.py novel check --project .work/lighthouse --scene ch1s1 --beat 3 --text-file editions/novel/examples/novel/beats/ch1s1-3.txt
python editions/novel/synapse_novel.py novel lock --project .work/lighthouse --scene ch1s1 --beat 3
~~~

각 `beat` 명령은 요청을 기록합니다. 글을 생성하거나 선택 인자 `--model`을 호출하지 않습니다. 각 `check`는 잠금 가능 여부를 반환하고 검사한 글과 영수증을 저장합니다. 각 `lock`은 터미널에서만 하며 사람이 자기 말을 입력해야 합니다. 같은 장면의 다음 비트는 앞 비트를 잠근 뒤 요청할 수 있습니다.

## 4. 상태 확인 및 조립

~~~sh
python editions/novel/synapse_novel.py novel status --project .work/lighthouse
python editions/novel/synapse_novel.py novel assemble --project .work/lighthouse --scene ch1s1 --out .work/lighthouse/ch1s1.md
~~~

세 비트가 모두 잠겼고 stale 항목이 없을 때만 새 파일을 씁니다. 이미 있는 출력 파일은 덮어쓰지 않습니다.

## 결과 용어 및 복구

사람이 읽는 메시지와 오류는 한국어입니다. JSON 키는 정확한 이름을 유지하며 필드 의미를 나타냅니다.

- `not_examined` = 잠금을 막는 원문 `미검토` 상태입니다.
- `unchecked` = 잠금을 막지는 않는 `미확인` 정본 값입니다. 터미널 잠금 단계에서 보여 주고 사람의 말과 함께 기록합니다.

잠긴 비트의 글, 확인 정본 블록, 계획 해시, 같은 장면의 앞선 비트가 바뀌면 잠금이 stale 상태가 됩니다. 계획 해시는 저장된 씨앗과 정본 값의 입력 지문을 포함합니다. 다음 순서로 복구하세요.

1. `status`를 읽고 장면별로 가장 앞선 stale 비트를 확인합니다.
2. 씨앗이나 정본 값이 바뀌었다면 `plan`을 다시 실행하고 터미널에서 새 계획 해시를 확인합니다.
3. 가장 앞선 stale 비트부터 시작합니다. 계획·정본 맥락 또는 앞선 비트가 바뀌었다면 `beat`를 다시 실행합니다. 글 파일을 검사하고 터미널에서 잠급니다. 해당 비트의 저장 글만 바뀌었다면 다시 검사하고 잠급니다.
4. 각 요청은 직전 잠금 비트에 기대므로 같은 장면의 뒤 비트도 순서대로 반복합니다.
5. status를 다시 읽습니다. 모든 비트가 최신이고 잠긴 경우에만 조립합니다.

로컬 MCP 서버는 기존 `SYNAPSE_NOVEL_ROOT` 폴더 아래만 읽고 씁니다. 프로젝트와 전달하는 씨앗·계획·글 입력을 모두 이 폴더 아래에 두세요. MCP에는 값·계획 확인, 비트 잠금, 장면 조립 도구가 없습니다.

[판본 안내서](../../README.ko.md), [English example](README.md), [中文示例](README.zh.md)를 보세요.
