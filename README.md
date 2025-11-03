# Django Attendance & Payroll App (公開テンプレート / Public Template)

## 🇯🇵 概要（日本語）

このアプリは、**勤怠管理と給与計算を一体化**した Django 製Webアプリです。  
QRコードでの出退勤登録、通常・休日・特別期間の自動集計、CSV出力、GUIでの給与設定などを備えています。  
ローカル環境で3分で起動できるよう設計されています。

---

### 🔧 主な機能
- QRコードで出勤・退勤を記録  
- 通常 / 休日 / 特別期間の自動集計  
- 給与計算（時給・固定給・控除対応）  
- CSV出力機能  
- Django管理画面からのユーザー管理  

---

### 🚀 クイックスタート
```bash
# 仮想環境を作成
python -m venv venv_attendance
source venv_attendance/bin/activate  # Windows: venv_attendance\Scripts\activate

# 必要ライブラリをインストール
pip install -r requirements.txt

# サンプル環境変数をコピー
cp env.sample .env

# マイグレーション
python manage.py migrate

# 管理ユーザー作成
python manage.py createsuperuser

# サーバー起動
python manage.py runserver

アクセス: http://localhost:8000

⚙️ 使用技術
	•	Python 3.13
	•	Django 4.2.x
	•	Bootstrap 5
	•	SQLite / PostgreSQL（選択可）
