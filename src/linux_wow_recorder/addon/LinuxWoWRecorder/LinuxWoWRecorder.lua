-- Linux WoW Recorder cuts its clips from the combat log, so this turns logging on wherever a
-- clip can come from: dungeons and raids. Other addons start it on zone changes only, and miss
-- a key when the dungeon becomes Mythic+ without one (zoned in before it had a difficulty).
-- So it checks again when the keystone slot opens, when the key starts, when the difficulty
-- changes, at a boss pull and after each loading screen. It never turns logging off.
--
-- It also notes what the combat log doesn't have, for the recorder's moment clips: when the
-- character levels up, and which NPCs are elite or rare (by NPC ID, seen on nameplates and
-- targets out in the world). WoW saves these at logout or /reload; the recorder reads them
-- once WoW is closed.

local KEEP_SECONDS = 30 * 24 * 3600   -- level-ups older than this are dropped
local ELITE = { elite = true, rareelite = true, rare = true, worldboss = true }

local function LogOn()
    local _, instanceType = GetInstanceInfo()
    if instanceType ~= "party" and instanceType ~= "raid" then
        return
    end
    if GetCVar("advancedCombatLogging") ~= "1" then
        SetCVar("advancedCombatLogging", "1")   -- the recorder reads mob health and positions
    end
    if not LoggingCombat() then
        LoggingCombat(true)
    end
end

local function Secret(value)
    return issecretvalue ~= nil and issecretvalue(value)
end

local function NoteUnit(unit)
    local db = LinuxWoWRecorderDB
    if not db or IsInInstance() or not UnitExists(unit) or UnitIsPlayer(unit) then
        return
    end
    local class = UnitClassification(unit)
    local guid = UnitGUID(unit)
    if Secret(class) or Secret(guid) or not ELITE[class] or not guid then
        return
    end
    local kind, _, _, _, _, npc = strsplit("-", guid)
    npc = tonumber(npc)
    if (kind == "Creature" or kind == "Vehicle") and npc then
        db.elites[npc] = class
    end
end

local function Load()
    LinuxWoWRecorderDB = LinuxWoWRecorderDB or {}
    local db = LinuxWoWRecorderDB
    db.elites = db.elites or {}
    local levels, now = {}, time()
    for _, entry in ipairs(db.levels or {}) do   -- "time:level"
        local t = tonumber((strsplit(":", entry)))
        if t and now - t < KEEP_SECONDS then
            table.insert(levels, entry)
        end
    end
    db.levels = levels
end

local frame = CreateFrame("Frame")
frame:SetScript("OnEvent", function(_, event, arg1)
    if event == "ADDON_LOADED" then
        if arg1 == "LinuxWoWRecorder" then
            Load()
        end
    elseif event == "PLAYER_LEVEL_UP" then
        if LinuxWoWRecorderDB then
            table.insert(LinuxWoWRecorderDB.levels, time() .. ":" .. tostring(arg1))
        end
    elseif event == "NAME_PLATE_UNIT_ADDED" then
        NoteUnit(arg1)
    elseif event == "PLAYER_TARGET_CHANGED" then
        NoteUnit("target")
    elseif event == "UPDATE_MOUSEOVER_UNIT" then
        NoteUnit("mouseover")
    else
        LogOn()
        if event == "PLAYER_ENTERING_WORLD" then
            C_Timer.After(3, LogOn)   -- the instance's type can come a moment after the loading screen
        end
    end
end)
for _, event in ipairs({
    "PLAYER_ENTERING_WORLD",
    "ZONE_CHANGED_NEW_AREA",
    "PLAYER_DIFFICULTY_CHANGED",
    "CHALLENGE_MODE_KEYSTONE_RECEPTABLE_OPEN",   -- Blizzard's spelling
    "CHALLENGE_MODE_START",
    "ENCOUNTER_START",
    "ADDON_LOADED",
    "PLAYER_LEVEL_UP",
    "NAME_PLATE_UNIT_ADDED",
    "PLAYER_TARGET_CHANGED",
    "UPDATE_MOUSEOVER_UNIT",
}) do
    frame:RegisterEvent(event)
end
