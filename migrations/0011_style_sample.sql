-- 文风样章：每本小说可单独填一段喜欢的文字，写正文时让 AI 模仿语气和节奏（不进其他任务的上下文）

ALTER TABLE `novel_setting` ADD COLUMN `style_sample` TEXT NULL AFTER `style`;
