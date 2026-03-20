-- pop_messages.lua
-- Atomically read and clear the message + image buffers.
-- Returns all buffered data and resets both buffers to empty arrays.
--
-- KEYS[1] = session:{user_id}
--
-- Returns: {messages_json, images_json}
--   messages_json: string — JSON array of buffered message texts
--   images_json: string — JSON array of buffered image URLs

local messages = redis.call('HGET', KEYS[1], 'messages') or '[]'
local images = redis.call('HGET', KEYS[1], 'images') or '[]'
redis.call('HSET', KEYS[1], 'messages', '[]')
redis.call('HSET', KEYS[1], 'images', '[]')
return {messages, images}
