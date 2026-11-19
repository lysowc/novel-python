-- 作者定下的要点：点子讨论里已经拍板的事（开场、视角、关键情节顺序、不要什么……）
-- 从点子一路带到小说设定，生成设定/大纲/正文时必须遵守，不再在「压缩成摘要」时丢掉

ALTER TABLE `idea` ADD COLUMN `decisions` TEXT NULL AFTER `content`;
ALTER TABLE `idea` ADD COLUMN `source_idea_id` INT UNSIGNED NULL AFTER `novel_id`;
ALTER TABLE `novel_setting` ADD COLUMN `decisions` TEXT NULL AFTER `novel_id`;
