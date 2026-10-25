-- ForcesLog: logs the Mythic+ enemy forces bar (its raw count) each time it moves, with the
-- time to the millisecond. tools/forces_check.py matches each rise to the mobs that died just
-- before it in the combat log, to check forces.py. Addons can't read the combat log in
-- Midnight, so the deaths come from the log file. Saved on /reload or logout.

local frame = CreateFrame("Frame")
local offset   -- time() - GetTime(), found when time() ticks over, so stamps have milliseconds
local run      -- the key being logged: ForcesLogDB.runs[n]

local function now()
    return offset and floor((GetTime() + offset) * 1000 + 0.5) / 1000 or time()
end

local function forces()
    local steps = select(3, C_Scenario.GetStepInfo()) or 0
    for i = 1, steps do
        local info = C_ScenarioInfo.GetCriteriaInfo(i)
        if info and info.isWeightedProgress then
            -- quantityString is the raw count ("245" or "245%"); quantity is a percent.
            local raw = tonumber((info.quantityString or ""):match("%d+"))
            return raw, info.totalQuantity
        end
    end
end

local function startRun()
    local map = C_ChallengeMode.GetActiveChallengeMapID()
    if not map then return end
    local level = C_ChallengeMode.GetActiveKeystoneInfo()
    run = {map = map, level = level, start = now(), steps = {}}
    table.insert(ForcesLogDB.runs, run)
end

local function update()
    if not run then
        if not C_ChallengeMode.GetActiveChallengeMapID() then return end
        startRun()   -- a /reload mid-key: a new entry from here
    end
    local raw, total = forces()
    if not raw then return end
    local last = run.steps[#run.steps]
    if not last or last[2] ~= raw then
        table.insert(run.steps, {now(), raw})
        run.total = total
    end
end

frame:SetScript("OnEvent", function(_, event, name)
    if event == "ADDON_LOADED" then
        if name ~= "ForcesLog" then return end
        ForcesLogDB = ForcesLogDB or {}
        ForcesLogDB.runs = ForcesLogDB.runs or {}
        -- Keeps the newest 10 keys of each dungeon.
        local seen = {}
        for i = #ForcesLogDB.runs, 1, -1 do
            local map = ForcesLogDB.runs[i].map
            seen[map] = (seen[map] or 0) + 1
            if seen[map] > 10 then table.remove(ForcesLogDB.runs, i) end
        end
        frame:UnregisterEvent("ADDON_LOADED")
        local second = time()
        frame:SetScript("OnUpdate", function()
            if time() ~= second then
                offset = time() - GetTime()
                frame:SetScript("OnUpdate", nil)
            end
        end)
    elseif event == "CHALLENGE_MODE_START" then
        startRun()
    elseif event == "CHALLENGE_MODE_COMPLETED" or event == "CHALLENGE_MODE_RESET" then
        update()
        run = nil
    else
        update()
    end
end)

frame:RegisterEvent("ADDON_LOADED")
frame:RegisterEvent("CHALLENGE_MODE_START")
frame:RegisterEvent("CHALLENGE_MODE_COMPLETED")
frame:RegisterEvent("CHALLENGE_MODE_RESET")
frame:RegisterEvent("SCENARIO_CRITERIA_UPDATE")
