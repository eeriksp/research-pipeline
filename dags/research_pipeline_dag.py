from datetime import datetime, timedelta
import os
import sys

# Airflow-related imports
from airflow import DAG
from airflow.operators.empty import EmptyOperator # Dummy operator
from airflow.operators.python import PythonOperator # Python operator
from airflow.providers.postgres.operators.postgres import PostgresOperator # PostgresOperator
# from airflow.operators.bash import BashOperator # Bash operator
from airflow.utils.dates import days_ago

# Import the custom scripts
from scripts.raw_to_tables import *
from scripts.augmentations import *
from scripts.final_tables import *
from scripts.sql_queries import *
from scripts.neo4j_queries import *

# Import necessary libraries
import pandas as pd # working with dataframes
import numpy as np # vector operations

### Misc
from math import floor
import time
import requests
import warnings # suppress warnings
import os # accessing directories
from tqdm import tqdm # track loop runtime
from unidecode import unidecode # international encoding fo names
import psycopg2
from datetime import date


#### ------ Python Functions ------  ####

## Check the date and delete
def delete_for_update():
    """Task for removing tables from 'data_ready'
    so that all .csv-s could be updated.
    """
    try:
        print('Removing previously cleaned tables for update...')
        os.remove('dags/data_ready/article.csv')
        os.remove('dags/data_ready/article_augmented.csv')
        os.remove('dags/data_ready/article_category.csv')
        os.remove('dags/data_ready/author.csv')
        os.remove('dags/data_ready/authorship.csv')
        os.remove('dags/data_ready/category.csv')
        os.remove('dags/data_ready/journal.csv')
        print('Succesfully removed tables for update!')
    except:
        print("Could not remove the 'data_ready' directory")

## Check if the uncleaned tables in the directory or prepare them
def find_tables_or_ingest_raw():
    """Function that searches if 'raw' .csv tables exist.
    If yes, nothing is done (will be used in next task).
    If no, data will be ingested and prepared.
    """
    print('Checking if tables are prepared as .csv files...')
    if os.path.exists('dags/tables/author.csv'):
        print("'author.csv' exists.")
        pass
    if os.path.exists('dags/tables/article.csv'):
        print("'article.csv' exists.")
        pass
    if os.path.exists('dags/tables/authorship.csv'):
        print("'authorship.csv' exists.")
        pass
    if os.path.exists('dags/tables/article_category.csv'):
        print("'article_category.csv' exists.")
        pass
    if os.path.exists('dags/tables/category.csv'):
        print("'category.csv' exists.")
        pass
    if os.path.exists('dags/tables/journal.csv'):
        print("'journal.csv' exists.")
        pass
        print('Tables exist in the directory!')

    ## If tables do not exist, pull from kaggle (or local machine), proprocess to tables
    else: 
        print('Tables are in the working directory!')
        print('Please run the data ingestion script from Terminal:')
        print('python -m pip install -r requirements.txt')
        print('python dags/scripts/raw_to_tables.py')
        sys.exit(1)

## Check if clean tables in the directory or prepare them
def check_or_augment():
    """Function to either check if clean tables exist
    or clean the data and write them to clean .csv-s.
    """
    print('Checking if clean tables exist...')

    print("Checking if 'article_augmented_raw' table exist...")
    if os.path.exists('dags/data_ready/article_augmented_raw.csv'):
        if os.path.exists('dags/data_ready/article.csv'):
            print("Checking if clean 'article' table exist...")
            article = pd.read_csv('dags/data_ready/article.csv', error_bad_lines=False)
            print("Augmented clean table 'article' exists!")
        else:
            article_journal = pd.read_csv('dags/data_ready/article_augmented_raw.csv', error_bad_lines=False)
            article = article_journal[article_journal['type'] == 'journal-article'].reset_index(drop = True)
            article.to_csv('dags/data_ready/article.csv', index = False)
    else:
        print("'article_augmented_raw'  not found. Perparing augmentation...")
        article = article_ready()
    
    # Journal
    journal = journal_ready()

    # Remove not found journals from articles
    article = article[article['journal_issn'].isin(journal['journal_issn'])].reset_index(drop = True)
    # Update 'article.csv' in 'data_ready' directory
    article.to_csv('dags/data_ready/article.csv', index = False)

    authorship = authorship_ready(article)
    author = author_ready(article, authorship)
    article_category = article_category_ready(article)
    category = category_ready(article_category)

## Insert into tables (helper function for DWH)
def insert_to_tables(cur, table, query):
    ''' Helper function for inserting values to Postresql tables
    Args:
        table (pd.DataFrame): pandas table
        query (SQL query): correspondive SQL query for 'table' for data insertion in DB
    '''
    print(f'Inserting table -- {table.name} -- ...')
    
    try:
        for i, row in table.iterrows():
            cur.execute(query, list(row))
        print(f'Table -- {table.name} -- successfully inserted!')
    except:
        print(f'Error with table -- {table.name} --')
    print()

## From pandas to Postgres
def pandas_to_dwh():
    """Task that imports .csv-s to pandas, makes the Postgres-connection,
    creates a database, drops existing and creates new tables, and inserts
    the data from pandas.
    """
    # Import the data
    try:
        article = pd.read_csv('dags/data_ready/article.csv', error_bad_lines=False)
        author = pd.read_csv('dags/data_ready/author.csv', error_bad_lines=False)
        authorship = pd.read_csv('dags/data_ready/authorship.csv', error_bad_lines=False)
        category = pd.read_csv('dags/data_ready/category.csv', error_bad_lines=False)
        article_category = pd.read_csv('dags/data_ready/article_category.csv', error_bad_lines=False)
        journal = pd.read_csv('dags/data_ready/journal.csv', error_bad_lines=False)
        tables = [article, author, authorship, category, article_category, journal]

        # Name of tables (for later print)
        article.name = 'article'
        author.name = 'author'
        authorship.name = 'authorship'
        category.name = 'category'
        article_category.name = 'article_category'
        journal.name = 'journal'
        print(article.head(2))
        print(author.head(2))
        print(authorship.head(2))
        print(category.head(2))
        print(article_category.head(2))
        print(journal.head(2))
        print('All tables staged for DWH.')
    except:
        print('Error with importing the data tables')
        sys.exit(1)
       
    # Connect to the database
    try: 
        print('Connecting to Postgres...')
        conn = psycopg2.connect(host="postgres", user="airflow", password="airflow", database ="airflow", port = 5432)
        conn.set_session(autocommit=True)
        cur = conn.cursor()
    except:
        print('Postgres connection not established')
        sys.exit(1)
        
    # Drop Tables 
    try: 
        for query in drop_tables:
            cur.execute(query)
            conn.commit()
        print('All tables dropped.')
    except:
        print('Error with dropping tables.')
            
    # Create Tables
    try: 
        for query in create_tables:
            cur.execute(query)
            conn.commit()
            print('All tables created.')
    except:
        print('Error with creating tables.')

    try:
        # Insert into tables
        for i in tqdm(range(len(tables))):
            insert_to_tables(cur, tables[i], insert_tables[i])
    except:
        print('Error in inserting the data.')
        print('Error in inserting the data.')

## From pandas to Neo4J
def pandas_to_neo():
    """Task that makes the connection with Neo4J database,
    imports cleaned .csv-s, tries to delete the previous relationships and nodes,
    creates unique ID constraints, inserts the nodes and relationships from pandas,
    and outputs some test queries (with count of nodes).
    """
    # Import the data
    try:
        article = pd.read_csv('dags/data_ready/article.csv', error_bad_lines=False)
        author = pd.read_csv('dags/data_ready/author.csv', error_bad_lines=False)
        authorship = pd.read_csv('dags/data_ready/authorship.csv', error_bad_lines=False)
        category = pd.read_csv('dags/data_ready/category.csv', error_bad_lines=False)
        article_category = pd.read_csv('dags/data_ready/article_category.csv', error_bad_lines=False)
        journal = pd.read_csv('dags/data_ready/journal.csv', error_bad_lines=False)
        tables = [article, author, authorship, category, article_category, journal]

        # Name of tables (for later print)
        article.name = 'article'
        author.name = 'author'
        authorship.name = 'authorship'
        category.name = 'category'
        article_category.name = 'article_category'
        journal.name = 'journal'
        print(article.head(2))
        print(author.head(2))
        print(authorship.head(2))
        print(category.head(2))
        print(article_category.head(2))
        print(journal.head(2))
        print('All tables staged for Neo4J.')
    except:
        print('Error with importing the data tables.')

    # Neo4J Connection
    try:
        print('Trying to establish Neo4J connection...')
        conn_neo = Neo4jConnection(uri='bolt://neo:7687', user='', pwd='')
        print('Neo4J Connection established!')
    except:
        print('Neo4J Connection not established...')
        sys.exit(1)
    try:   
        # Warm up the start by caching the database
        ## Read more here: https://neo4j.com/developer/kb/warm-the-cache-to-improve-performance-from-cold-start/
        ### 1st query
        result_warmup1 = conn_neo.query("""
        MATCH (n)
        OPTIONAL MATCH (n)-[r]->()
        RETURN count(n.prop) + count(r.prop)
        """)
        print(f'Warm-up query result: {result_warmup1}')

        result_warmup2 = conn_neo.query('MATCH (n:Article) RETURN COUNT(n) AS ct')
        print(result_warmup2[0]['ct'])

        result_warmup3 = conn_neo.query('MATCH (n:Author) RETURN COUNT(n) AS ct')
        print(result_warmup3[0]['ct'])

        result_warmup4 = conn_neo.query('MATCH (n:Journal) RETURN COUNT(n) AS ct')
        print(result_warmup4[0]['ct'])

        result_warmup5 = conn_neo.query('MATCH (n:Category) RETURN COUNT(n) AS ct')
        print(result_warmup5[0]['ct'])

        result_warmup4 = conn_neo.query("""
        MATCH (n)
        OPTIONAL MATCH (n:Author)-[r:AUTHORED]->(n2:Article)
        RETURN count(r)
        """)
        print(result_warmup4)
    except:
        print('Error while running warm-up queries.')
        sys.exit(1)

    # Set constraints
    try:
        print('Setting constraints to unique IDs...')
        # Add ID uniqueness constraint to optimize queries
        conn_neo.query('CREATE CONSTRAINT ON(n:Category) ASSERT n.id IS UNIQUE')
        conn_neo.query('CREATE CONSTRAINT ON(j:Journal) ASSERT j.id IS UNIQUE')
        conn_neo.query('CREATE CONSTRAINT ON(au:Author) ASSERT au.id IS UNIQUE')
        conn_neo.query('CREATE CONSTRAINT ON(ar:Article) ASSERT ar.id IS UNIQUE')
        print('Constraints to unique IDs successfully set!')
    except:
        print('Could not set constraints.')
        sys.exit(1)




    print(f'Inserting pandas to Neo4J...')
    try: 
        print("Adding 'category' nodes to Neo4J...")
        add_category(conn_neo, category)
        print("'category' added to Neo4J!")
    except:
        print("Could not add 'category' to Neo4J")
        print("This can be a connection issue (see above) or the data already exists (see below)")
        
    try: 
        print("Adding 'journal' nodes to Neo4J...")
        add_journal(conn_neo, journal)
        print("'journal' added to Neo4J!")
    except: 
        print("Could not add 'journal' nodes to Neo4J")    
        print("This can be a connection issue (see above) or the data already exists (see below)")

    try: 
        print("Adding 'article' nodes to Neo4J...")
        add_article(conn_neo, article)
        print("'article' added to Neo4J!")
    except: 
        print("Could not add 'article' to Neo4J")  
        print("This can be a connection issue (see above) or the data already exists (see below)")

    try: 
        print("Adding 'author' nodes to Neo4J...")
        add_author(conn_neo, author)
        print("'author' added to Neo4J!")
    except: 
        print("Could not add 'author' to Neo4J")     
        print("This can be a connection issue (see above) or the data already exists (see below)") 

    try: 
        print("Adding 'article_category' relationship to Neo4J...")
        add_article_category(conn_neo, article_category)
        print("'article_category' added to Neo4J!")
    except: 
        print("Could not add 'article_category' to Neo4J")  
        print("This can be a connection issue (see above) or the data already exists (see below)")

    try: 
        print("Adding 'authorship' relationship to Neo4J...")
        add_authorship(conn_neo, authorship)
        print("'authorship' added to Neo4J!")
    except: 
        print("Could not add 'authorship' to Neo4J")
        print("This can be a connection issue (see above) or the data already exists (see below)")
    try:
        print("Adding 'co-authorship' relation to Neo4J...")
        conn_neo.query("""
                MATCH (author1:Author) - [:AUTHORED] -> (article:Article) <-[:AUTHORED] - (author2:Author)
                CREATE (author1)-[new:COAUTHORS]->(author2)
                RETURN type(new);
            """)
        print("Added co-authorship relations.")
    except:
        print("Failed to add co-authorship relations.")

    try:
       print("Adding article-journal relation to Neo4J...")
       conn_neo.query("""
       MATCH (article:Article), (j:Journal) 
       WHERE article.journal_issn = j.id
       CREATE (article)-[:PUBLISHED_IN]->(j) 
       RETURN article, j
       """)
       print("Added article-journal relations.")
    except:
        print("Failed to add articl-journal relations.")

        print('Below are the counts of entities in the Neo4J database (must be non-null):')
        n_articles = conn_neo.query('MATCH (n:Article) RETURN COUNT(n) AS ct')
        n_authors = conn_neo.query('MATCH (n:Author) RETURN COUNT(n) AS ct')
        n_journals = conn_neo.query('MATCH (n:Journal) RETURN COUNT(n) AS ct')   
        n_categories =  conn_neo.query('MATCH (n:Category) RETURN COUNT(n) AS ct')  
            
        print(f"Number of articles in the Neo4J database: {n_articles[0]['ct']}")
        print(f"Number of authors in the Neo4J database: {n_authors[0]['ct']}")
        print(f"Number of journals in the Neo4J database: {n_journals[0]['ct']}")
        print(f"Number of categories in the Neo4J database: {n_categories[0]['ct']}")

        result_warmup4 = conn_neo.query("""
        MATCH (n)
        OPTIONAL MATCH (n:Author)-[r:AUTHORED]->(n2:Article)
        RETURN count(r)
        """)
        print(f'Number of author-article relationships {result_warmup4}')

#### ------ AIRFLOW ------  ####
# Cron notation: https://cloud.google.com/scheduler/docs/configuring/cron-job-schedules
## Also: https://crontab.guru/

# Adding default parameters:
default_args = {
    'owner': 'dmitri_rozgonjuk',
    'depends_on_past': False, # The DAG does not have dependencies on past runs
    'retries': 4, # On failure, the task are retried 4 times
   # 'schedule_interval': '0 0 1 7 *', # Schedule interval yearly to execute on 00:00 01.08
    'retry_delay': timedelta(minutes = 2), # Retries happen every 5 minutes
    'catchup' : False, # Catchup is turned off
    'email_on_retry': False, # Do not email on retry
    'email_on_failure': False, # Also, do not email on failure
    'start_date': datetime(2022, 8, 1), # set starting day in the past
    'schedule_interval': None # '@yearly' # run yearly
}

# Define the DAG
dag = DAG('research_pipeline_dag',
          default_args=default_args,
          description= 'Run the Research Data Pipeline and Prepare Databases',
        )

# Define the tasks
## Starting the DAG
start_operator = EmptyOperator(task_id='Begin_Execution',  dag = dag)

## Find tables or ingest raw data
ingest_task1 = PythonOperator(task_id='find_tables_or_ingest_raw', python_callable = find_tables_or_ingest_raw, dag = dag)

## Load the data or augment and save as csv
augment_task2 = PythonOperator(task_id='check_or_augment', python_callable = check_or_augment, dag = dag)

## Make the connection with Postgres, load pandas tables to DWH
postgres_task3 = PythonOperator(task_id='pandas_to_dwh', python_callable = pandas_to_dwh, dag = dag)

# Neo4J Connection and data load
neo_task4 = PythonOperator(task_id='pandas_to_neo', python_callable = pandas_to_neo, dag = dag)

# Delete the data and prepare for updates
prepare_for_update_task5 = PythonOperator(task_id='delete_for_update', python_callable = delete_for_update, dag = dag)

## Ending the DAG
end_operator = EmptyOperator(task_id='Stop_Execution',  dag = dag)

# Create task dependencies/pipeline
## Initially, data load to Postgres and Neo4J was parallel - but this produced errors (memory issues)
start_operator >> prepare_for_update_task5 >> ingest_task1 >> augment_task2 >> postgres_task3 >> neo_task4 >> end_operator
# augment_task2 >> postgres_task3 >> end_operator
# augment_task2 >> neo_task3 >> end_operator