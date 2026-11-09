
import shutil
from datetime import datetime
from pathlib import Path

class BackupManager:
    def __init__(self, backup_dir=None):
        if backup_dir:
            self.backup_dir = Path(backup_dir)
        else:
            self.backup_dir = Path(__file__).resolve().parents[1] / "backups"
        self.backup_dir.mkdir(parents=True, exist_ok=True)

    def create_backup(self, db_path):
        """创建数据库备份"""
        db_path = Path(db_path)
        if not db_path.exists():
            return {"status": "error", "message": f"数据库文件不存在: {db_path}"}

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_name = f"{db_path.stem}_{timestamp}.db"
        backup_path = self.backup_dir / backup_name

        try:
            shutil.copy2(db_path, backup_path)
            return {
                "status": "success",
                "backup_path": str(backup_path),
                "backup_name": backup_name,
                "size_kb": round(backup_path.stat().st_size / 1024, 2),
                "created_at": datetime.now().isoformat()
            }
        except OSError as e:
            return {"status": "error", "message": f"备份失败: {str(e)}"}

    def restore_backup(self, backup_path, target_db):
        """从备份恢复数据库"""
        backup_path = Path(backup_path)
        target_db = Path(target_db)

        if not backup_path.exists():
            return {"status": "error", "message": f"备份文件不存在: {backup_path}"}

        try:
            # 先备份目标文件（如果存在）
            safety_backup_name = None
            if target_db.exists():
                safety_backup_name = (
                    f"{target_db.stem}_before_restore_"
                    f"{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.db"
                )
                backup_before = self.backup_dir / safety_backup_name
                shutil.copy2(target_db, backup_before)

            shutil.copy2(backup_path, target_db)
            return {
                "status": "success",
                "message": f"数据库已从 {backup_path.name} 恢复",
                "restored_to": str(target_db),
                "safety_backup_name": safety_backup_name,
                "restored_at": datetime.now().isoformat()
            }
        except OSError as e:
            return {"status": "error", "message": f"恢复失败: {str(e)}"}

    def list_backups(self):
        """列出所有备份文件"""
        backups = []
        for backup_file in sorted(self.backup_dir.glob("*.db")):
            try:
                backups.append({
                    "name": backup_file.name,
                    "path": str(backup_file),
                    "size_kb": round(backup_file.stat().st_size / 1024, 2),
                    "created_at": datetime.fromtimestamp(backup_file.stat().st_mtime).isoformat()
                })
            except OSError:
                continue

        backups.sort(key=lambda x: x["created_at"], reverse=True)
        return {"backups": backups, "count": len(backups)}

    def delete_backup(self, backup_name):
        """删除指定备份"""
        backup_path = self.backup_dir / backup_name

        if not backup_path.exists():
            return {"status": "error", "message": f"备份文件不存在: {backup_name}"}

        try:
            backup_path.unlink()
            return {"status": "success", "message": f"已删除备份: {backup_name}"}
        except OSError as e:
            return {"status": "error", "message": f"删除失败: {str(e)}"}

    def cleanup_old_backups(self, keep_days=7):
        """清理过期备份"""
        cutoff_time = datetime.now().timestamp() - (keep_days * 24 * 60 * 60)
        deleted_count = 0

        for backup_file in self.backup_dir.glob("*.db"):
            try:
                if backup_file.stat().st_mtime < cutoff_time:
                    backup_file.unlink()
                    deleted_count += 1
            except OSError:
                continue

        return {
            "status": "success",
            "deleted_count": deleted_count,
            "cleaned_at": datetime.now().isoformat()
        }

backup_manager = BackupManager()
