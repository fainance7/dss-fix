import requests
import psycopg2
from datetime import datetime, timedelta
from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.models import Variable

def sync_kato():
    url   = Variable.get("DSS_API_URL")
    token = Variable.get("DSS_API_TOKEN")
    
    resp = requests.get(f"{url}/api/v1/kato",
                        headers={"Authorization": f"Bearer {token}"},
                        timeout=30)
    resp.raise_for_status()
    data = resp.json()
    
    conn = psycopg2.connect(Variable.get("REPORTS_DB_CONN_SQLALCHEMY").replace("postgresql+psycopg2://", "postgresql://"))
    cur  = conn.cursor()
    for row in data:
        cur.execute("""
            INSERT INTO kato_cache (kato_code, name_ru, updated_at)
            VALUES (%s, %s, NOW())
            ON CONFLICT (kato_code) DO UPDATE SET name_ru=EXCLUDED.name_ru, updated_at=NOW()
        """, (row.get("kato_code"), row.get("name_ru")))
    conn.commit()
    cur.close(); conn.close()
    print(f"Синхронизировано KATO: {len(data)} записей")

def sync_indicators():
    url   = Variable.get("DSS_API_URL")
    token = Variable.get("DSS_API_TOKEN")
    
    resp = requests.get(f"{url}/api/v1/indicators",
                        headers={"Authorization": f"Bearer {token}"},
                        timeout=30)
    resp.raise_for_status()
    data = resp.json()
    
    conn = psycopg2.connect(Variable.get("REPORTS_DB_CONN_SQLALCHEMY").replace("postgresql+psycopg2://", "postgresql://"))
    cur  = conn.cursor()
    for row in data:
        cur.execute("""
            INSERT INTO indicator_cache (indicator_name, direction_code, unit_name, updated_at)
            VALUES (%s, %s, %s, NOW())
            ON CONFLICT (indicator_name) DO UPDATE
            SET direction_code=EXCLUDED.direction_code, updated_at=NOW()
        """, (row.get("indicator_name"), row.get("direction_code"), row.get("unit_name")))
    conn.commit()
    cur.close(); conn.close()
    print(f"Синхронизировано indicators: {len(data)} записей")

with DAG("dag_sync_metadata",
         default_args={"owner": "airflow", "retries": 2,
                       "retry_delay": timedelta(minutes=5)},
         schedule_interval="0 2 * * *",
         start_date=datetime(2024, 1, 1),
         catchup=False,
         tags=["mdm"]) as dag:

    t1 = PythonOperator(task_id="sync_kato",       python_callable=sync_kato)
    t2 = PythonOperator(task_id="sync_indicators", python_callable=sync_indicators)
    t1 >> t2
