-- migration 0004: login OTP storage for ownership-proof flow (H2r)

CREATE TABLE login_otps (
	id VARCHAR(36) NOT NULL,
	email VARCHAR(320) NOT NULL,
	code_hash VARCHAR(64) NOT NULL,
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
	attempts INTEGER DEFAULT 0 NOT NULL,
	consumed_at TIMESTAMP WITH TIME ZONE,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id)
);

CREATE INDEX idx_login_otps_email ON login_otps (email);
