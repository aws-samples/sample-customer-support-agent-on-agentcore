-- append_and_increment.lua
-- Append message + images to buffer, increment version atomically.
--
-- KEYS[1] = session:{user_id}
-- ARGV[1] = new message text
-- ARGV[2] = current timestamp
-- ARGV[3] = images JSON array (e.g. '["https://img1.jpg"]' or '[]')
--
-- Returns: {new_version, prev_state}
--   new_version: integer — the incremented version counter
--   prev_state: string — session state before this call ("idle" | "processing" | "consultant")

local prev_state = redis.call('HGET', KEYS[1], 'state') or 'idle'
local new_version = redis.call('HINCRBY', KEYS[1], 'version', 1)

-- Append message to buffer
local messages = redis.call('HGET', KEYS[1], 'messages') or '[]'
local msg_decoded = cjson.decode(messages)
table.insert(msg_decoded, ARGV[1])
redis.call('HSET', KEYS[1], 'messages', cjson.encode(msg_decoded))

-- Accumulate images (merge new images into existing array)
local new_images = cjson.decode(ARGV[3])
if #new_images > 0 then
    local images = redis.call('HGET', KEYS[1], 'images') or '[]'
    local img_decoded = cjson.decode(images)
    for _, img in ipairs(new_images) do
        table.insert(img_decoded, img)
    end
    redis.call('HSET', KEYS[1], 'images', cjson.encode(img_decoded))
end

redis.call('HSET', KEYS[1], 'last_updated', ARGV[2])
redis.call('EXPIRE', KEYS[1], 300)

return {new_version, prev_state}
