-- AI 日志记录每次调用的参数（温度、思考强度、输出上限）和完整输入/输出/思考过程，便于排查和搜索

ALTER TABLE `ai_log` ADD COLUMN `temperature` DECIMAL(4,2) NULL AFTER `duration`;
ALTER TABLE `ai_log` ADD COLUMN `reasoning` VARCHAR(16) NOT NULL DEFAULT '' AFTER `temperature`;
ALTER TABLE `ai_log` ADD COLUMN `max_tokens` INT UNSIGNED NOT NULL DEFAULT 0 AFTER `reasoning`;
ALTER TABLE `ai_log` ADD COLUMN `input_text` LONGTEXT NULL AFTER `prompt`;
ALTER TABLE `ai_log` ADD COLUMN `output_text` LONGTEXT NULL AFTER `input_text`;
ALTER TABLE `ai_log` ADD COLUMN `reasoning_text` LONGTEXT NULL AFTER `output_text`;
ALTER TABLE `ai_log` ADD KEY `idx_novel_task` (`novel_id`, `task_type`);
