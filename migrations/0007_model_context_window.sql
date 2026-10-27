-- 模型上下文窗口（tokens）：填写后自动计算章节生成的上下文长度上限

ALTER TABLE `ai_model` ADD COLUMN `context_window` INT UNSIGNED NOT NULL DEFAULT 0 AFTER `max_tokens`;
