import motor.motor_asyncio
import asyncio
import pprint
from bson import SON
client=motor.motor_asyncio.AsyncIOMotorClient("localhost",27017)
db=client.ta
collection=db.her

async def do_insert():
    
    result=await db.her.insert_many([{"i":i} for i in range(2000)])
    print("inserted %d docs"%(len(result.inserted_ids),))

async def do_find_one():
    document=await db.her.find_one({"i":{"$lt":1}})
    pprint.pprint(document)

async def do_find():
    c=db.her
    async for document in c.find({"i":{"$lt":2}}):
        pprint.pprint(document)
async def do_count():
    n=await db.her.count_documents({})
    print("%s documents in collection"%n)
    n=await db.her.count_documents({"i":{"$gt":1000}})
    print("%s documents where i>1000"%n)

async def do_replace():
    coll =db.her
    old_document=await coll.find_one({"i":50})
    print("found document:%s"%pprint.pformat(old_document))
    _id=old_document["_id"]
    result=await coll.replace_one({"_id":_id},{"key":"value"})
    print("replaced %s document" %result.modified_count)
    new_document=await coll.find_one({"_id":_id})
    print("document is now %s"%pprint.pformat(new_document))
async def do_udpate():
    coll=db.her
    result=await coll.update_one({"i":51},{"$set":{"key":"value"}})
    print("updated %s document"%result.modified_count)
    new_document=await coll.find_one({"i":51})
    print("document is now %s"%pprint.pformat(new_document))

async def do_delete_one():
    coll=db.her
    n=await coll.count_documents({})
    print("%s documents before calling delete_one()" %n)
    result=await db.her.delete_one({"i":{"$gte":1000}})
    print("%s documents after" %(await coll.count_documents({})))
async def do_delete_many():
    coll=db.her
    n=await coll.count_documents({})
    print("%s documents before calling delete_many()" %n)
    result=await db.her.delete_many({"i":{"$gte":1000}})
    print("%s documents after" %(await coll.count_documents({})))

async def use_distinct_command():
    response=await db.command(SON([("distinct","test_collection"),("key","i")]))
    
loop=client.get_io_loop()
loop.run_until_complete(use_distinct_command())
