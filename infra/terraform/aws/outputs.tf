output "instance_id" {
  description = "EC2 instance ID (use with AWS Systems Manager Session Manager)."
  value       = aws_instance.host.id
}

output "public_ip" {
  description = "Public IP. Inbound traffic is blocked unless allowed_https_cidrs is set."
  value       = aws_instance.host.public_ip
}

output "ssm_port_forward_command" {
  description = "Open the UI through an encrypted SSM tunnel at http://localhost:8089."
  value       = "aws ssm start-session --region ${var.region} --target ${aws_instance.host.id} --document-name AWS-StartPortForwardingSession --parameters portNumber=8089,localPortNumber=8089"
}
