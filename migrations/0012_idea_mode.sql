-- 点子的聊法：refine = 打磨点子（围绕已有想法追问细节）；explore = 随便聊聊（还没有明确点子，一起找方向）

ALTER TABLE `idea` ADD COLUMN `mode` VARCHAR(16) NOT NULL DEFAULT 'refine' AFTER `status`;
