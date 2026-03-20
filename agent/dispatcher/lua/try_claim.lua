-- try_claim.lua
-- Atomically claim the right to invoke AgentCore for this version.
-- Only succeeds if the caller's version matches the current version in Redis.
--
-- KEYS[1] = session:{user_id}
-- ARGV[1] = my_version (the version this handler was assigned)
-- ARGV[2] = my_request_id (UUID for logging/tracing)
--
-- Returns: 1 if claimed, 0 if superseded by a newer message

local current_version = tonumber(redis.call('HGET', KEYS[1], 'version'))
if current_version == tonumber(ARGV[1]) then
    redis.call('HSET', KEYS[1], 'state', 'processing')
    redis.call('HSET', KEYS[1], 'request_id', ARGV[2])
    return 1
end
return 0
