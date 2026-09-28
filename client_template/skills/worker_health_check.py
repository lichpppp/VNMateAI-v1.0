from core.plugin_manager import export_skill

@export_skill(name="worker_health_check", description="Kiểm tra sức khỏe máy trạm", parameters_schema={"type": "object", "properties": {}})
def worker_health_check() -> dict:
    return {"status": "success", "message": "Máy trạm hoàn toàn khỏe mạnh và sẵn sàng!"}