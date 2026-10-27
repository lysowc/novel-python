-- 用量与费用统计：AI 日志记录所属小说、费用、是否估算；模型增加单价（每百万 tokens）

ALTER TABLE `ai_log` ADD COLUMN `novel_id` INT UNSIGNED NOT NULL DEFAULT 0 AFTER `id`;
ALTER TABLE `ai_log` ADD COLUMN `cost` DECIMAL(14,6) NOT NULL DEFAULT 0 AFTER `total_tokens`;
ALTER TABLE `ai_log` ADD COLUMN `usage_estimated` TINYINT NOT NULL DEFAULT 0 AFTER `cost`;
ALTER TABLE `ai_log` ADD KEY `idx_novel` (`novel_id`);
ALTER TABLE `ai_model` ADD COLUMN `price_input` DECIMAL(10,4) NOT NULL DEFAULT 0 AFTER `temperature`;
ALTER TABLE `ai_model` ADD COLUMN `price_output` DECIMAL(10,4) NOT NULL DEFAULT 0 AFTER `price_input`;
