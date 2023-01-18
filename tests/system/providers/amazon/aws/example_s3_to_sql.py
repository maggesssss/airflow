# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.
from __future__ import annotations

from datetime import datetime

from airflow import DAG
from airflow.decorators import task
from airflow.exceptions import AirflowException
from airflow.models.baseoperator import chain
from airflow.providers.amazon.aws.operators.s3 import (
    S3CreateBucketOperator,
    S3DeleteBucketOperator,
    S3DeleteObjectsOperator,
    S3CreateObjectOperator,
)
from airflow.providers.amazon.aws.transfers.s3_to_sql import S3ToSqlOperator
from airflow.providers.common.sql.operators.sql import SQLExecuteQueryOperator
from airflow.utils.trigger_rule import TriggerRule
from tests.system.providers.amazon.aws.utils import SystemTestContextBuilder, ENV_ID_KEY
import os

sys_test_context_task = SystemTestContextBuilder().build()

DAG_ID = "example_s3_to_sql"

SQL_TABLE_NAME = "cocktails"
SQL_COLUMN_LIST = ["cocktail_id", "cocktail_name", "base_spirit"]
SAMPLE_DATA = """
1;Caipirinha;Cachaca\n
2;Bramble;Gin\n
3;Daiquiri;Rum
"""

with DAG(
    dag_id=DAG_ID,
    start_date=datetime(2023, 1, 1),
    schedule="@once",
    catchup=False,
    tags=["example"],
) as dag:

    test_context = sys_test_context_task()
    env_id = test_context[ENV_ID_KEY]

    s3_bucket_name = f"{env_id}-bucket"
    s3_key = f"{env_id}/files/cocktail_list.csv"

    create_bucket = S3CreateBucketOperator(
        task_id="create_bucket",
        bucket_name=s3_bucket_name,
    )

    create_object = S3CreateObjectOperator(
        task_id="create_object",
        s3_bucket=s3_bucket_name,
        s3_key=s3_key,
        data=SAMPLE_DATA,
        replace=True,
    )

    create_table = SQLExecuteQueryOperator(
        task_id="create_sample_table",
        sql=f"""
            CREATE TABLE IF NOT EXISTS {SQL_TABLE_NAME} (
            cocktail_id INT NOT NULL,
            cocktail_name VARCHAR NOT NULL,
            base_spirit VARCHAR NOT NULL);
          """,
    )

    # [START howto_transfer_s3_to_sql]
    #
    # This operator requires a parser method. The Parser should take a filename as input
    # and return an iterable of rows.
    # This example parser uses the builtin csv library and returns a list of rows
    #
    def parse_csv_to_list(filepath):
        import csv

        with open(filepath, newline="") as file:
            return [row for row in csv.reader(file)]

    transfer_s3_to_sql = S3ToSqlOperator(
        task_id="transfer_s3_to_sql",
        s3_bucket=s3_bucket_name,
        s3_key=s3_key,
        table=SQL_TABLE_NAME,
        column_list=SQL_COLUMN_LIST,
        parser=parse_csv_to_list,
    )
    # [END howto_transfer_s3_to_sql]

    # [START howto_transfer_s3_to_sql_generator]
    #
    # As the parser can return any kind of iterator, a generator is also allowed.
    # This example parser returns a generator which prevents python from loading
    # the whole file into memory.
    #

    def parse_csv_to_generator(filepath):
        import csv

        with open(filepath, newline="") as file:
            yield from csv.reader(file)

    transfer_s3_to_sql_generator = S3ToSqlOperator(
        task_id="transfer_s3_to_sql_paser_to_generator",
        s3_bucket=s3_bucket_name,
        s3_key=s3_key,
        table=SQL_TABLE_NAME,
        column_list=SQL_COLUMN_LIST,
        parser=parse_csv_to_generator,
    )
    # [END howto_transfer_s3_to_sql_generator]

    drop_table = SQLExecuteQueryOperator(
        trigger_rule=TriggerRule.ALL_DONE, task_id="drop_table", sql=f"DROP TABLE {SQL_TABLE_NAME}"
    )

    delete_s3_objects = S3DeleteObjectsOperator(
        trigger_rule=TriggerRule.ALL_DONE,
        task_id="delete_objects",
        bucket=s3_bucket_name,
        keys=s3_key,
    )

    delete_s3_bucket = S3DeleteBucketOperator(
        trigger_rule=TriggerRule.ALL_DONE,
        task_id="delete_bucket",
        bucket_name=s3_bucket_name,
        force_delete=True,
    )

    @task(trigger_rule=TriggerRule.ONE_FAILED, retries=0)
    def watcher():
        raise AirflowException("Failing task because one or more upstream tasks failed.")

    chain(
        # TEST SETUP
        test_context,
        create_bucket,
        create_object,
        create_table,
        # TEST BODY
        transfer_s3_to_sql,
        transfer_s3_to_sql_generator,
        # TEST TEARDOWN
        drop_table,
        delete_s3_objects,
        delete_s3_bucket,
    )

    list(dag.tasks) >> watcher()

from tests.system.utils import get_test_run

# Needed to run the example DAG with pytest (see: tests/system/README.md#run_via_pytest)
test_run = get_test_run(dag)