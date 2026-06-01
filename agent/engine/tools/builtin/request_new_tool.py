from langchain_core.tools import tool

from agent.engine import creation_pipeline


@tool
def request_new_tool(user_intent: str) -> str:
    """
    [메타 도구] 기존 등록 도구로 처리할 수 없는 요청일 때 호출합니다.

    user_intent 에는 사용자가 원하는 작업을 자연어로 명확히 기술하세요.
    예: "피벗 테이블 생성", "월별 추이 차트", "특정 컬럼들 사이의 상관계수 표".

    이 도구를 호출하면 orchestrator 가 creation_pipeline 으로 분기하여
    새 도구의 사양 정의·코드 생성·검증·등록을 자동으로 수행합니다.

    주의:
    - 한 사용자 요청에 **딱 한 번만** 호출하세요. 반복 호출은 무시됩니다.
    - 기존 도구로 해결 가능한 요청에는 호출하지 마세요.
    """
    creation_pipeline.set_pending(user_intent)
    return (
        f"요청 의도 '{user_intent}' 가 접수되었습니다. "
        "orchestrator 가 새 도구 생성 파이프라인으로 분기합니다."
    )
