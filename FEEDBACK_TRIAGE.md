# Synapse Novel feedback triage

This guide turns a privacy-safe report into a reproducible work item. It does not promise a response time or judge literary quality.

## English

Choose one primary category and add a secondary category only when the evidence shows a dependency.

| Category | Use it for | First evidence |
|---|---|---|
| Installation | Python, PyYAML, launcher, file permissions, moved checkout | Version, OS, interpreter path, command |
| Seed or plan | Seed shape, proposed keys, plan validation, confirmation hash | Sanitized seed structure, proposed key, plan hash, exact command |
| Beat flow | Request order, text check, lockability, stale lock, status, assembly | Scene and beat numbers, redacted result fields, current or stale state |
| MCP connection | Client registration, stdio startup, registered-folder refusal | Client, server command, registered-folder path shape (redacted), exact error |
| Result meaning | Korean message, JSON field, advisory quality result, documented limit | Exact message, unchanged JSON keys and redacted values, exit code |

Triage in this order:

1. Stop handling a report that contains a secret or private story content. Ask for a redacted replacement.
2. Reproduce with the bundled synthetic example in a clean or known-good environment.
3. If the example passes, reduce the report to the smallest sanitized input that still shows the problem.
4. Compare the command or MCP tool, version, exit code, JSON fields, Korean message, and stale/next-step state.
5. Keep the writer's text and the check result separate. A structural check does not judge literary quality.

Use these work states: `RECEIVED`, `NEEDS_INFO`, `REPRODUCED`, `FIXED_PENDING_RECHECK`, `VERIFIED`, and `NOT_REPRODUCED`. `NOT_REPRODUCED` is not `FIXED`; it means more evidence is needed or the issue remains environment-specific.

## 한국어

이 안내는 개인정보를 지운 신고를 재현 가능한 작업으로 정리합니다. 응답 시간을 약속하거나 문학적 품질을 판정하지 않습니다.

증거에 따라 1차 범주를 하나만 고르고, 실제 의존성이 드러날 때만 2차 범주를 추가합니다.

| 범주 | 해당 문제 | 먼저 볼 증거 |
|---|---|---|
| 설치 | Python, PyYAML, 실행 파일, 파일 권한, 이동한 체크아웃 | 버전, OS, 인터프리터 경로, 명령 |
| 씨앗·계획 | 씨앗 형식, 제안 키, 계획 검사, 확인 해시 | 비식별 씨앗 구조, 제안 키, 계획 해시, 명령 |
| 비트 흐름 | 요청 순서, 글 검사, 잠금 조건, stale 잠금, 상태, 조립 | 장면·비트 번호, 개인정보를 지운 결과 필드, 최신/stale 상태 |
| MCP 연결 | 클라이언트 등록, stdio 시작, 등록 폴더 거부 | 클라이언트, 서버 명령, 비식별 등록 경로 형태, 정확한 오류 |
| 결과 의미 | 한국어 메시지, JSON 필드, 품질 조언, 문서 한계 | 정확한 메시지, 변경하지 않은 JSON 키와 비식별 값, 종료 코드 |

다음 순서로 처리합니다.

1. 비밀값이나 비공개 소설 내용이 들어오면 처리를 멈추고 개인정보를 지운 자료를 요청합니다.
2. 깨끗하거나 정상임을 아는 환경에서 동봉된 합성 예제로 재현합니다.
3. 예제가 통과하면 문제를 보여 주는 가장 작은 비식별 입력으로 줄입니다.
4. 명령 또는 MCP 도구, 버전, 종료 코드, JSON 필드, 한국어 메시지, stale/다음 단계 상태를 비교합니다.
5. 작성자의 글과 검사 결과를 구분합니다. 구조 검사는 문학적 품질을 판정하지 않습니다.

작업 상태는 `RECEIVED`, `NEEDS_INFO`, `REPRODUCED`, `FIXED_PENDING_RECHECK`, `VERIFIED`, `NOT_REPRODUCED`를 사용합니다. `NOT_REPRODUCED`는 `FIXED`가 아닙니다. 증거가 더 필요하거나 환경에 따라 달라지는 상태입니다.

## 简体中文

本指南将已脱敏的问题报告整理为可复现事项。它不承诺响应时间，也不评价文学质量。

选择一个主要类别；只有证据显示存在依赖时才添加次要类别。

| 类别 | 适用问题 | 首先查看的证据 |
|---|---|---|
| 安装 | Python、PyYAML、启动器、文件权限、移动后的 checkout | 版本、操作系统、解释器路径、命令 |
| 种子或计划 | 种子格式、提议键、计划验证、确认哈希 | 已脱敏的种子结构、提议键、计划哈希、准确命令 |
| 节拍流程 | 请求顺序、文本检查、锁定条件、stale 锁、状态、合成 | 场景和节拍编号、脱敏结果字段、当前或 stale 状态 |
| MCP 连接 | 客户端注册、stdio 启动、注册文件夹拒绝 | 客户端、服务器命令、脱敏后的注册路径形状、准确错误 |
| 结果含义 | 韩语消息、JSON 字段、质量建议、文档边界 | 准确消息、未改动的 JSON 键及脱敏值、退出码 |

按以下顺序分流：

1. 报告含有秘密或私人小说内容时立即停止处理，并请求脱敏替代内容。
2. 在干净或已知正常的环境中用随附的合成示例复现。
3. 如果示例通过，将输入缩减到仍能显示问题的最小脱敏内容。
4. 对照命令或 MCP 工具、版本、退出码、JSON 字段、韩语消息以及 stale/下一步状态。
5. 将创作者的文本与检查结果分开。结构检查不会评价文学质量。

工作状态使用 `RECEIVED`、`NEEDS_INFO`、`REPRODUCED`、`FIXED_PENDING_RECHECK`、`VERIFIED` 和 `NOT_REPRODUCED`。`NOT_REPRODUCED` 不等于 `FIXED`；它表示仍需更多证据或问题仅在特定环境出现。
