output "instance_id" {
  value = aws_instance.k3s_node.id
}

output "public_ip" {
  value = aws_instance.k3s_node.public_ip
}

output "ssh_command" {
  value = "ssh -i ~/.ssh/discord-bot-key.pem ubuntu@${aws_instance.k3s_node.public_ip}"
}
