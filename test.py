'''
Author: yancy yancyshang@duck.com
Date: 2024-01-05 15:01:32
LastEditors: yancy yancyshang@duck.com
LastEditTime: 2024-01-05 15:15:16
FilePath: \chatgpt-telegram-bot\test.py
Description: 
'''
from pymongo import MongoClient


def get_database():
 
  
    CONNECTION_STRING="mongodb+srv://yan:IRZxBerRaGZeRC1b@cluster0.ebkdzfm.mongodb.net/?retryWrites=true&w=majority"
    
 
   # Create a connection using MongoClient. You can import MongoClient or use pymongo.MongoClient
    client = MongoClient(CONNECTION_STRING)
 
   # Create the database for our example (we will use the same database throughout the tutorial
    return client['user_shopping_list']
  
# This is added so that many files can reuse the function get_database()
if __name__ == "__main__":   
  
   # Get the database
   dbname = get_database()
   dbname.get_collections()
   print(dbname.list_collection_names())
  