-- Pure helpers (no vim.* calls) so they can be unit-tested under plenary/busted or plain lua.
local M = {}

local LOOPBACK = { ["localhost"] = true, ["127.0.0.1"] = true, ["[::1]"] = true }

--- Tokens travel over https, or http to loopback only. Returns normalized base or nil, err.
function M.safe_base(url)
  if type(url) ~= "string" then return nil, "url is not a string" end
  local scheme, rest = url:match("^(%a[%w+.-]*)://(.+)$")
  if not scheme then return nil, "url is not valid" end
  scheme = scheme:lower()
  local authority = rest:match("^([^/]*)")
  if authority:find("@", 1, true) then return nil, "url must not embed credentials" end
  local host = authority:match("^(%[.-%])") or authority:match("^([^:]+)")
  if scheme ~= "https" and not (scheme == "http" and LOOPBACK[(host or ""):lower()]) then
    return nil, "https is required (http is allowed only for localhost)"
  end
  return (url:gsub("/+$", "")), nil
end

function M.redact(text, token)
  text = text:gsub("[Bb][Ee][Aa][Rr][Ee][Rr]%s+[%w%._~%+/=%-]+", "Bearer [REDACTED]")
  if token and #token >= 6 then
    local s, e = text:find(token, 1, true)
    while s do
      text = text:sub(1, s - 1) .. "[REDACTED]" .. text:sub(e + 1)
      s, e = text:find(token, s + 10, true)
    end
  end
  return text
end

--- curl --config body fed on stdin: the token never appears in the process argv.
function M.curl_config(method, url, token, body)
  local function q(s) return '"' .. s:gsub("\\", "\\\\"):gsub('"', '\\"'):gsub("\n", "\\n") .. '"' end
  local lines = {
    "request = " .. q(method),
    "url = " .. q(url),
    "header = " .. q("authorization: Bearer " .. token),
    "header = " .. q("content-type: application/json"),
    "max-time = 15",
    "silent",
    "show-error",
    "write-out = " .. q("\n%{http_code}"),
  }
  if body then lines[#lines + 1] = "data = " .. q(body) end
  return table.concat(lines, "\n") .. "\n"
end

--- Splits curl output into (body_text, status_number).
function M.split_response(out)
  local body, status = out:match("^(.*)\n(%d%d%d)%s*$")
  if not body then return nil, 0 end
  return body, tonumber(status)
end

function M.friendly(status, code, message)
  if status == 401 then return "Your Harness session is invalid or expired. Sign in again." end
  if status == 403 then return "Not allowed for your role (" .. code .. ")." end
  if status == 404 then return "Not found, or not visible to your team." end
  if status == 409 then return "Conflict (" .. code .. "): " .. message end
  return message .. " (" .. code .. ")"
end

return M
