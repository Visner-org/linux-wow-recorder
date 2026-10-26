-- Linux WoW Recorder cuts its clips from the combat log, so this turns logging on wherever a
-- clip can come from: dungeons and raids. Other addons start it on zone changes only, and miss
-- a key when the dungeon becomes Mythic+ without one (zoned in at Heroic, then the key went in).
-- So it checks again when the keystone slot opens, when the key starts, when the difficulty
-- changes, at a boss pull and after each loading screen. It never turns logging off.

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

local frame = CreateFrame("Frame")
frame:SetScript("OnEvent", function(_, event)
    LogOn()
    if event == "PLAYER_ENTERING_WORLD" then
        C_Timer.After(3, LogOn)   -- the instance's type can come a moment after the loading screen
    end
end)
for _, event in ipairs({
    "PLAYER_ENTERING_WORLD",
    "ZONE_CHANGED_NEW_AREA",
    "PLAYER_DIFFICULTY_CHANGED",
    "CHALLENGE_MODE_KEYSTONE_RECEPTABLE_OPEN",   -- Blizzard's spelling
    "CHALLENGE_MODE_START",
    "ENCOUNTER_START",
}) do
    frame:RegisterEvent(event)
end
